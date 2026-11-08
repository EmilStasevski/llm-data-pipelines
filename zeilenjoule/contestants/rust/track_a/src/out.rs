//! Output: the exact Track A schema (`spec/track_a.schema.json`), a columnar builder and Parquet writers.

use crate::Date;
use arrow_array::builder::{
    BooleanBuilder, Date32Builder, Float64Builder, Int8Builder, Int32Builder, StringBuilder,
};
use arrow_array::{ArrayRef, RecordBatch};
use arrow_schema::{ArrowError, DataType, Field, Schema, SchemaRef};
use parquet::arrow::ArrowSchemaConverter;
use parquet::arrow::arrow_writer::{
    ArrowColumnChunk, ArrowColumnWriter, ArrowRowGroupWriterFactory, compute_leaves,
};
use parquet::basic::{Compression, ZstdLevel};
use parquet::errors::ParquetError;
use parquet::file::properties::{WriterProperties, WriterPropertiesPtr};
use parquet::file::writer::SerializedFileWriter;
use std::fs::File;
use std::path::Path;
use std::sync::{Arc, LazyLock};

pub static SCHEMA: LazyLock<SchemaRef> = LazyLock::new(|| {
    let s = |n: &str| Field::new(n, DataType::Utf8, true);
    Arc::new(Schema::new(vec![
        s("meldung_id"),
        s("werk"),
        s("anlage"),
        s("equipment_nr"),
        Field::new("datum", DataType::Date32, true),
        s("schicht"),
        Field::new("prioritaet", DataType::Int8, true),
        Field::new("stillstand_min", DataType::Int32, true),
        Field::new("kosten_eur", DataType::Float64, true),
        s("status"),
        s("text_norm"),
        s("text_lower"),
        s("text_fold"),
        s("materialnr"),
        Field::new("sicherheitsrelevant_rule", DataType::Boolean, true),
        s("komponente_rule"),
        s("regierungsbezirk"),
        s("nuts2"),
        s("doc_text"),
    ]))
});

/// One output row, borrowed.
pub struct Row<'a> {
    pub meldung_id: &'a str,
    pub werk: &'a str,
    pub anlage: &'a str,
    pub equipment_nr: Option<&'a str>,
    pub datum: Option<Date>,
    pub schicht: &'a str,
    pub prioritaet: Option<i8>,
    pub stillstand_min: Option<i32>,
    pub kosten_eur: Option<f64>,
    pub status: &'a str,
    pub text_norm: &'a str,
    pub text_lower: &'a str,
    pub text_fold: &'a str,
    pub materialnr: Option<&'a str>,
    pub sicherheitsrelevant_rule: bool,
    pub komponente_rule: Option<&'a str>,
    pub regierungsbezirk: Option<&'a str>,
    pub nuts2: Option<&'a str>,
    pub doc_text: &'a str,
}

/// Columnar builder for one batch.
pub struct Builder {
    s: [StringBuilder; 14], // string columns in schema order (without datum/prio/minutes/eur/bool)
    datum: Date32Builder,
    prio: Int8Builder,
    minutes: Int32Builder,
    eur: Float64Builder,
    safety: BooleanBuilder,
}

impl Builder {
    #[must_use]
    pub fn with_capacity(rows: usize) -> Self {
        Self {
            s: std::array::from_fn(|_| StringBuilder::with_capacity(rows, rows * 24)),
            datum: Date32Builder::with_capacity(rows),
            prio: Int8Builder::with_capacity(rows),
            minutes: Int32Builder::with_capacity(rows),
            eur: Float64Builder::with_capacity(rows),
            safety: BooleanBuilder::with_capacity(rows),
        }
    }

    pub fn push(&mut self, r: &Row<'_>) {
        let [
            id,
            werk,
            anlage,
            eq,
            schicht,
            status,
            tn,
            tl,
            tf,
            mat,
            komp,
            rb,
            nuts,
            doc,
        ] = &mut self.s;
        id.append_value(r.meldung_id);
        werk.append_value(r.werk);
        anlage.append_value(r.anlage);
        eq.append_option(r.equipment_nr);
        self.datum.append_option(r.datum.map(Date::days));
        schicht.append_value(r.schicht);
        self.prio.append_option(r.prioritaet);
        self.minutes.append_option(r.stillstand_min);
        self.eur.append_option(r.kosten_eur);
        status.append_value(r.status);
        tn.append_value(r.text_norm);
        tl.append_value(r.text_lower);
        tf.append_value(r.text_fold);
        mat.append_option(r.materialnr);
        self.safety.append_value(r.sicherheitsrelevant_rule);
        komp.append_option(r.komponente_rule);
        rb.append_option(r.regierungsbezirk);
        nuts.append_option(r.nuts2);
        doc.append_value(r.doc_text);
    }

    /// # Errors
    /// Arrow errors (cannot happen with a consistent builder).
    pub fn finish(mut self) -> Result<RecordBatch, ArrowError> {
        let [
            id,
            werk,
            anlage,
            eq,
            schicht,
            status,
            tn,
            tl,
            tf,
            mat,
            komp,
            rb,
            nuts,
            doc,
        ] = &mut self.s;
        let sb = |b: &mut StringBuilder| -> ArrayRef { Arc::new(b.finish()) };
        let cols: Vec<ArrayRef> = vec![
            sb(id),
            sb(werk),
            sb(anlage),
            sb(eq),
            Arc::new(self.datum.finish()),
            sb(schicht),
            Arc::new(self.prio.finish()),
            Arc::new(self.minutes.finish()),
            Arc::new(self.eur.finish()),
            sb(status),
            sb(tn),
            sb(tl),
            sb(tf),
            sb(mat),
            Arc::new(self.safety.finish()),
            sb(komp),
            sb(rb),
            sb(nuts),
            sb(doc),
        ];
        RecordBatch::try_new(SCHEMA.clone(), cols)
    }
}

/// Writer properties used by all Rust contestants: zstd (level 1, the pyarrow default).
///
/// # Panics
/// Never (level 1 is a valid zstd level).
#[must_use]
pub fn props() -> WriterPropertiesPtr {
    Arc::new(
        WriterProperties::builder()
            .set_compression(Compression::ZSTD(
                ZstdLevel::try_new(1).expect("zstd level"),
            ))
            .build(),
    )
}

/// Parquet file writer whose row groups are encoded elsewhere (in parallel) and appended in order.
pub struct ParallelWriter {
    w: SerializedFileWriter<File>,
    ctx: EncodeCtx,
}

/// Everything a worker thread needs to encode one row group.
#[derive(Clone)]
pub struct EncodeCtx {
    factory: Arc<ArrowRowGroupWriterFactory>,
}

impl EncodeCtx {
    /// Encode one batch into closed, compressed column chunks (CPU-heavy part, thread-safe).
    ///
    /// # Errors
    /// Parquet/arrow encoding errors.
    pub fn encode(
        &self,
        batch: &RecordBatch,
        row_group: usize,
    ) -> Result<Vec<ArrowColumnChunk>, ParquetError> {
        let mut writers = self.factory.create_column_writers(row_group)?;
        for ((w, field), col) in writers.iter_mut().zip(SCHEMA.fields()).zip(batch.columns()) {
            for leaf in compute_leaves(field, col)? {
                w.write(&leaf)?;
            }
        }
        writers.into_iter().map(ArrowColumnWriter::close).collect()
    }
}

impl ParallelWriter {
    /// # Errors
    /// File creation / schema conversion errors.
    pub fn create(path: &Path) -> Result<Self, ParquetError> {
        let props = props();
        let parquet_schema = ArrowSchemaConverter::new().convert(&SCHEMA)?;
        let mut kv = props.as_ref().clone();
        parquet::arrow::add_encoded_arrow_schema_to_metadata(&SCHEMA, &mut kv);
        let props = Arc::new(kv);
        let w = SerializedFileWriter::new(
            File::create(path)?,
            parquet_schema.root_schema_ptr(),
            props,
        )?;
        let factory = Arc::new(ArrowRowGroupWriterFactory::new(&w, SCHEMA.clone()));
        Ok(Self {
            w,
            ctx: EncodeCtx { factory },
        })
    }

    #[must_use]
    pub fn ctx(&self) -> EncodeCtx {
        self.ctx.clone()
    }

    /// Append one encoded row group (call in output order).
    ///
    /// # Errors
    /// I/O errors.
    pub fn append(&mut self, chunks: Vec<ArrowColumnChunk>) -> Result<(), ParquetError> {
        let mut rg = self.w.next_row_group()?;
        for c in chunks {
            c.append_to_row_group(&mut rg)?;
        }
        rg.close()?;
        Ok(())
    }

    /// # Errors
    /// I/O errors.
    pub fn close(self) -> Result<(), ParquetError> {
        self.w.close().map(|_| ())
    }
}

/// `meldung_id` column of an output batch.
///
/// # Panics
/// If the batch does not have the output schema.
#[must_use]
pub fn ids(batch: &RecordBatch) -> &arrow_array::StringArray {
    batch
        .column(0)
        .as_any()
        .downcast_ref()
        .expect("meldung_id is Utf8")
}

/// Batches are strictly increasing by `meldung_id`, within and across batches (spec: output sorted).
#[must_use]
pub fn strictly_sorted(batches: &[RecordBatch]) -> bool {
    let mut last: Option<&str> = None;
    for b in batches {
        for id in ids(b).iter().flatten() {
            if last.is_some_and(|l| id <= l) {
                return false;
            }
            last = Some(id);
        }
    }
    true
}
