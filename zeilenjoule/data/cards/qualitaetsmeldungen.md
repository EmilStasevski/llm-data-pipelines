# Datensatzkarte / Dataset card: `qualitaetsmeldungen` (synthetisch / synthetic)

> Generiert von `task data:cards`. Zahlen stammen aus den Daten. / Generated; numbers come from the data.

**Was / what:** 8D-/Reklamationsmeldungen mit Merkmal, Messwert, Toleranz und Urteil; das Urteil folgt aus den sichtbaren Werten (getestet). / quality notifications; the verdict follows from the visible values.

**Umfang / size:** 0.1 × Skalenzeilen (S → 1 000 …). Lieferanten: 150 fiktive Namen (Faker de_DE, geseedet, nur Rechtsformen GmbH/AG/KG/…).

## Dateien / files
| Skala / scale | Zeilen / rows | cp1252.csv | sha256 (cp1252.csv) |
|---|---|---|---|
| S | 1 000 | 0.2 MB | `1a33678fb926cafd…` |
| M | 10 000 | 2.0 MB | `60eea2a08402ce14…` |
| L | 100 000 | 20.3 MB | `42a853eff69c1c43…` |
| XL | 1 000 000 | 202.8 MB | `d508025b7c0e0d63…` |

## Fehlerklassen auf Skala XL / defect classes
| fehlerklasse | n | Anteil |
|---|---|---|
| Maßabweichung | 273546 | 27.35 % |
| Funktionsfehler | 159155 | 15.92 % |
| Materialfehler | 158877 | 15.89 % |
| Oberflächenfehler | 158739 | 15.87 % |
| kein Fehler | 149905 | 14.99 % |
| Verpackungsschaden | 50158 | 5.02 % |
| Kennzeichnungsfehler | 49620 | 4.96 % |

## Formate / formats
- Messwert `12,70 mm` oder `12,70mm`; Toleranz `12,50 ± 0,20 mm`, `12,30–12,70 mm`, `12,50 +0,20/-0,20 mm`, `max. 0,050 mm`, `<= 0,050 mm`, `min. 500 MPa` (`≤` ist nicht cp1252, daher `<=`/`max.`).
- Ursachenkategorie nach 6M (Mensch, Maschine, Material, Methode, Messung, Mitwelt) oder null.
- Messwerte halten mindestens eine Rundungseinheit Abstand zur Grenze (Rundung kann das Urteil nie kippen).
