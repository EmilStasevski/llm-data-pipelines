# Gold data quality (ESEF, Austrian filings)

Tables between `generated` markers come from `uv run ruhrbench gold-report`. Do not edit them by hand.

## Summary

- The gold is usable after removing one quarantined report, UniCredit Bank Austria FY2020, which is filed twice.
- Counts below use distinct reports. Counting filings would overstate the amount of independent gold.
- These are Austrian filings, not NRW companies. NRW gold is still to do.

## Size

<!-- generated:size -->
- Filings: 40
- Distinct reports (company + year): 34
- Companies: 18
- Extra filings of a report already counted (language versions, re-filings): 6
- KPI values in gold: 212
- Reports with a following-year filing to compare against: 16/34
- Filings quarantined: 2
<!-- /generated -->

## Coverage per KPI

<!-- generated:coverage -->
| kpi | filings | reports | report_coverage |
|---|---|---|---|
| revenue | 25/40 | 22/34 | 65% |
| net_income | 40/40 | 34/34 | 100% |
| total_assets | 40/40 | 34/34 | 100% |
| equity | 37/40 | 32/34 | 94% |
| cash | 40/40 | 34/34 | 100% |
| operating_income | 30/40 | 25/34 | 74% |
<!-- /generated -->

<!-- generated:missing -->
| company | kpi | years | similar_concepts (first 3) |
|---|---|---|---|
| Addiko Bank AG | revenue | 2020, 2021 | ifrs-full:InterestRevenueCalculatedUsingEffectiveInterestMethod, ifrs-full:InterestRevenueExpense |
| Bank für Tirol und Vorarlberg Aktiengesellschaft | operating_income | 2020, 2021 | ifrs-full:CashFlowsFromUsedInOperatingActivities, ifrs-full:DividendsReceivedClassifiedAsOperatingActivities, ifrs-full:IncomeTaxesPaidClassifiedAsOperatingActivities |
| Bank für Tirol und Vorarlberg Aktiengesellschaft | revenue | 2020, 2021 | btv:RevenueFromFinancialTransactions, ifrs-full:InterestRevenueCalculatedUsingEffectiveInterestMethod, ifrs-full:InterestRevenueExpense |
| HYPO NOE Landesbank für Niederösterreich und Wien AG | operating_income | 2020, 2021 | ifrs-full:CashFlowsFromUsedInOperatingActivities, ifrs-full:IncomeTaxesPaidRefundClassifiedAsOperatingActivities, ifrs-full:InterestPaidClassifiedAsOperatingActivities |
| HYPO NOE Landesbank für Niederösterreich und Wien AG | revenue | 2020, 2021 | ifrs-full:InterestRevenueCalculatedUsingEffectiveInterestMethod, ifrs-full:RevenueFromDividends |
| RAIFFEISENLANDESBANK NIEDERÖSTERREICH- | operating_income | 2020, 2021 | ifrs-full:CashFlowsFromUsedInOperatingActivities, ifrs-full:DividendsReceivedClassifiedAsOperatingActivities, ifrs-full:IncomeTaxesPaidRefundClassifiedAsOperatingActivities |
| RAIFFEISENLANDESBANK NIEDERÖSTERREICH- | revenue | 2020, 2021 | ifrs-full:InterestRevenueCalculatedUsingEffectiveInterestMethod, ifrs-full:InterestRevenueExpense, ifrs-full:OtherCashReceiptsFromSalesOfEquityOrDebtInstrumentsOfOtherEntitiesClassifiedAsInvestingActivities |
| Raiffeisen-Landesbank Steiermark | revenue | 2020, 2021 | ifrs-full:InterestRevenueCalculatedUsingEffectiveInterestMethod, ifrs-full:InterestRevenueExpense, ifrs-full:RevenueFromDividends |
| UniCredit Bank Austria AG | equity | 2020, 2021 | ext:TotalEquityAttributableToOwnersOfParent |
| UniCredit Bank Austria AG | operating_income | 2020, 2021 | ext:CashFlowsFromUsedInFinancialAssetsClassifiedAsOperatingActivities, ext:CashFlowsFromUsedInFinancialLiabilitiesClassifiedAsOperatingActivities, ext:OperatingCosts |
| UniCredit Bank Austria AG | revenue | 2020, 2021 | ext:InterestIncomeAndSimilarRevenues, ext:ProceedsFromSalesOfTangibleAssetsClassifiedAsInvestingActivities, ifrs-full:InterestRevenueCalculatedUsingEffectiveInterestMethod |
| Österreichischen Post AG | operating_income | 2020 | ifrs-full:CashFlowsFromUsedInOperatingActivities, oesterreichischepost:EarningsBeforeOtherFinancialResultAndIncomeTaxEBIT, oesterreichischepost:OtherOperatingIncome |
<!-- /generated -->

A missing KPI is mostly not a parsing failure. Almost all gaps are at banks, which do not report revenue or operating income as such. Filling them with interest income would change what the KPI means, so they stay missing.

Two gaps are tagged under a filer-specific name, not the standard IFRS concept: UniCredit's equity (`ext:`) and Österreichische Post's FY2020 EBIT. Aliases are a decision for the gold extension, not this report.

## Cross-year check

<!-- generated:cross_summary -->
| status | pairs |
|---|---|
| differs | 5 |
| ok | 77 |
| scale_mismatch | 2 |
<!-- /generated -->

<!-- generated:cross_detail -->
| company | fy_end | kpi | value | comparative | next_fy_end | status |
|---|---|---|---|---|---|---|
| POLYTEC GROUP | 2020-12-31 | equity | 247487000.0 | 234843000.0 | 2021-12-31 | differs |
| POLYTEC GROUP | 2020-12-31 | total_assets | 581504000.0 | 568861000.0 | 2021-12-31 | differs |
| Borealis AG | 2020-12-31 | operating_income | 355889000.0 | 317566000.0 | 2021-12-31 | differs |
| Borealis AG | 2020-12-31 | revenue | 6818000000.0 | 5785982000.0 | 2021-12-31 | differs |
| UniCredit Bank Austria AG | 2020-12-31 | cash | 95000.0 | 944000000.0 | 2021-12-31 | differs |
| UniCredit Bank Austria AG | 2020-12-31 | net_income | 15000.0 | 15000000.0 | 2021-12-31 | scale_mismatch |
| UniCredit Bank Austria AG | 2020-12-31 | total_assets | 118510000.0 | 118510000000.0 | 2021-12-31 | scale_mismatch |
<!-- /generated -->

This check compares each report's current-year value with the prior-year column of the following year's report. It only covers reports that have a following-year filing.

- **UniCredit Bank Austria FY2020 is a filer error.** The report prints `Bilanz (Mio €)`, but every numeric fact in the filing is tagged `scale=3, decimals=0`, so all values are 1000 times too low. The FY2021 filing uses `scale=6`. Gold for this report is wrong, and a model that reads the printed table correctly would be scored as a scale error.
- **UniCredit cash** also differs beyond scale (`95` printed, against a comparative of `944` in FY2021). The cause is not yet known. The report is quarantined anyway.
- **Borealis FY2020 and Polytec FY2020 are real restatements.** Their FY2021 reports label the comparatives "rückwirkend angepasst" and "angepasst". Gold is correct for the filing it came from, so a filing's gold must never be scored against another filing's comparatives.

## Tagged scale vs printed unit

<!-- generated:scale_summary -->
| status | filings |
|---|---|
| mismatch | 2 |
| ok | 38 |
<!-- /generated -->

<!-- generated:scale_detail -->
| filing | tag_scale | printed_scale | status |
|---|---|---|---|
| D1HEB8VEU6D9M8ZUXG17-2020-12-31-ESEF-AT-0 | 3 | 6 | mismatch |
| D1HEB8VEU6D9M8ZUXG17-2020-12-31-ESEF-AT-1 | 3 | 6 | mismatch |
<!-- /generated -->

This second check also covers each company's latest filing, which the cross-year check cannot. It compares the scale tagged on total assets with the unit printed around that number. It finds the unit by pattern matching and checks only that one fact per filing, so a pass is evidence, not proof.

## Other checks

Same concept and period tagged with different values:

<!-- generated:conflicts -->
| filing | concept | period_start | period_end | raw |
|---|---|---|---|---|
| 529900OVSOBJNXZACW81-2020-12-31-ESEF-AT-0 | ifrs-full:Equity | 2020-12-31 | 2020-12-31 | 247.486 vs 247.487 |
<!-- /generated -->

Accounting violations:

<!-- generated:identities -->
None.
<!-- /generated -->

Language versions or re-filings with different KPIs:

<!-- generated:twins -->
None.
<!-- /generated -->

## Quarantine

<!-- generated:quarantine -->
- `D1HEB8VEU6D9M8ZUXG17-2020-12-31-ESEF-AT-0`
- `D1HEB8VEU6D9M8ZUXG17-2020-12-31-ESEF-AT-1`
<!-- /generated -->

A filing is quarantined when either check proves a scale error. Both checks independently flag the same report. The filings stay on disk, but must not be used for scoring. The gold loader does not enforce this yet.

## Hand checks

<!-- generated:hand_checks -->
| risk | company | file | kpi | gold_eur | printed | tag_scale | printed_scale | text_around | matches_pdf |
|---|---|---|---|---|---|---|---|---|---|
| scale | UniCredit Bank Austria AG | D1HEB8VEU6D9M8ZUXG17-2020-12-31-ESEF-AT-0 | total_assets | 118,510,000 | 118.510 | 3 | 6 | swerte und Vermögensgruppen81782130. Sonstige Aktiva337353Summe Aktiva118.510101.663 | [ ] |
| sign | PORR AG | 529900ARBU9SBJFNX419-2020-12-31-ESEF-AT-0 | net_income | -42,367,000 | -42.367 | 3 | 3 | Jahresfehlbetrag/-überschuss -42.367 27.833 | [ ] |
| year (FY ends September) | Energie AG Oberösterreich | 529900N8OA1GVSR5R946-2022-09-30-ESEF-AT-1 | revenue | 3,808,230,200 | 3.808.230,2 | 3 | 3 |  für Stromeigenhandel (6) -193.860,1 -118.964,6 Nettoumsatzerlöse (6) 3.808.230,2 2.026.198,9 2. Veränderung des Bestands | [ ] |
| restatement | Borealis AG | 549300TAT2TADBW0WR70-2020-12-31-ESEF-AT-0 | revenue | 6,818,000,000 | 6.818.000 | 3 | 3 | chluss und KonzernabschlussIn TEUR 2020 2019 AnhangangabeUmsatzerlöse 6.818.000 8.102.872 1, 2Herstellungskosten -5.501 | [ ] |
<!-- /generated -->

**To do by hand:** open each company's published PDF report, find the number, and tick `matches_pdf`. `printed` is the number as it appears in the XHTML, so it can be searched for. The XHTML side has been read from the raw files; the comparison with the PDF has not been done.

What each row should show:

- **scale:** the PDF should say total assets of about 118.5 billion EUR, proving the gold value is wrong.
- **sign:** the PDF should show a net loss for FY2020.
- **year:** the value should sit in the 2021/2022 column of a fiscal year ending 30 September.
- **restatement:** the FY2020 PDF should show the gold value, not the lower comparative printed a year later.
