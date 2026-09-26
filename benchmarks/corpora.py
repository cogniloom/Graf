"""Deterministic benchmark inputs, with gold returned separately from searchable files.

The document corpus is a FICTIONAL REALISTIC SIMULATION, never customer evidence.
It exercises planted, manually authored scenarios, not population-level accuracy.
The code corpus preserves public-looking tracked UTF-8 source/docs from one HEAD;
filename exclusions are a conservative export filter, not a secret-scanner guarantee.
"""

from __future__ import annotations

import subprocess
from datetime import date, timedelta
from pathlib import Path, PurePosixPath

LABEL = "FICTIONAL REALISTIC SIMULATION — not customer evidence."

# Each record has ordinary surrounding context; questions and answer keys are never
# written into the searchable corpus. Dates, organizations and people are fictional.
DOCUMENTS = {
    "procurement/01_request.md": """# Request PR-417: Northstar cold-chain sensors
From: Mara Voss, facilities; Date: 2025-02-03
The pilot serves the East depot, where manual temperature checks delay dispatch.
Requested: 120 sensors from Vale Instruments for EUR 48,000 excluding VAT.
The request is a planning estimate, not an authorization to place an order.
Installation training is included; cellular subscriptions are billed separately.
Procurement must obtain finance approval before issuing the purchase order.
""",
    "procurement/02_approval.txt": """Decision record FIN-82 | 2025-02-07 | signed by Lena Ortiz, finance director
Reference: PR-417, Northstar East depot sensor pilot.
Approved ceiling: EUR 42,000 excluding VAT for 100 sensors.
Only Lena Ortiz may approve an increase to this ceiling.
This approval replaces the quantities in the request, not the warranty terms.
Purchasing will retain the acceptance checklist with the receiving record.
""",
    "procurement/03_amendment.md": """# Amendment A1 to FIN-82
Signed: Lena Ortiz, 2025-02-12; acknowledged by Vale Instruments on 2025-02-13.
A1 increases the approved ceiling to EUR 46,200 excluding VAT for 110 sensors.
A1 takes effect on 2025-02-15; all other FIN-82 terms remain unchanged.
The added ten sensors cover the annex; no approval is granted for West depot.
The supplier must identify the amendment on its final invoice.
""",
    "procurement/04_supplier_email.txt": """From: purchasing@northstar.example | To: dispatch@vale.example
Sent: 2025-02-14 16:20 UTC | Subject: Please hold PR-417 shipment
The annex team would prefer 120 units, but that is not an approved quantity.
Do not ship before the A1 effective date of 2025-02-15.
Please retain the original packaging schedule and send serial numbers after dispatch.
This email does not amend FIN-82 or A1.
""",
    "procurement/05_receipt.txt": """Goods receipt GR-991 | East depot | 2025-02-20
PR-417 / FIN-82 A1 / Vale Instruments
Received 110 sensors; 3 failed incoming inspection and were quarantined.
Accepted into service: 107 sensors. Replacement of the 3 units remains open.
Receiving clerk: Tomas Reed. Carton seals were intact on arrival.
Finance should hold the disputed units pending supplier credit or replacement.
""",
    "procurement/06_invoice.md": """# Invoice reconciliation note INV-804
Dated 2025-02-21; analyst: Priya Das; currency EUR, excluding VAT.
Vale invoiced EUR 46,200 for 110 sensors against PR-417.
The unit price is EUR 420; the disputed amount for 3 rejected sensors is EUR 1,260.
No payment release is recorded in this reconciliation note.
The freight charge is zero under the pilot agreement.
""",
    "incident/01_alert.txt": """INC-73 operations log | 2025-03-04 | all times UTC
08:12 — Monitor detected elevated checkout errors in the Birch region.
08:17 — On-call engineer Nia Chen acknowledged the alert.
08:24 — Change CHG-209 was identified as a possible contributor, not a confirmed cause.
The duty manager requested a read-only database health check before rollback.
This log records observation times; it is not a final root-cause analysis.
""",
    "incident/02_bridge.txt": """INC-73 bridge notes | 2025-03-04 | all times UTC
08:31 — Rollback of CHG-209 began after the duty manager approved it.
08:39 — Rollback completed; errors remained above baseline.
08:47 — Connection pool limit increased from 40 to 80.
08:56 — Error rate returned to baseline and stayed there for the observation window.
The communications lead will distinguish mitigation from root-cause confirmation.
""",
    "incident/03_review.md": """# INC-73 review, approved 2025-03-07
The customer-visible degradation lasted from 08:12 to 08:56 UTC on 2025-03-04.
Rollback alone did not restore service; recovery followed the pool-limit increase.
Root cause remains unconfirmed because database sampling was disabled during the event.
Action: Nia Chen will enable sampling by 2025-03-14; owner acceptance is pending.
No evidence of lost orders was found in the reviewed queue, but payment-provider logs were unavailable.
""",
    "versions/retention_v1.md": """# Retention policy RP-9, version 1
Approved 2024-11-01; archived copy retained for audit.
Diagnostic logs are retained for 90 days.
This copy predates the privacy review and must not be used for new deployments.
Owner: platform operations. Scope: diagnostic logs, excluding financial ledgers.
""",
    "versions/retention_v2.md": """# Retention policy RP-9, version 2
Approved by privacy council 2025-01-10; effective 2025-02-01.
Diagnostic logs are retained for 30 days; this supersedes version 1.
Financial ledger retention is outside the scope of RP-9.
Deployment teams must update the scheduled deletion job and retain its audit result.
""",
    "versions/retention_draft.md": """# Retention policy RP-9, version 3 DRAFT
Edited 2025-03-02. Discussion copy only; not approved and not effective.
Proposal: retain diagnostic logs for 14 days after the next infrastructure migration.
The privacy council has not voted; version 2 remains the approved policy.
Comments requested from incident response because shorter retention may affect investigations.
""",
    "people/directory.txt": """Internal directory extract | issued 2025-04-01
Alex Kim (employee E-104) works in procurement, based in Dublin.
Alex Kim (employee E-208) works in platform engineering, based in Oslo.
Both appear as A. Kim in older minutes; initials alone do not identify either employee.
For expense approvals use the employee identifier, not display name matching.
This extract contains only fictional staff contact metadata.
""",
    "people/signoff.txt": """Minutes: loading-bay access review | 2025-04-03
Attendees: facilities chair, security liaison, A. Kim.
A. Kim approved the loading-bay access change; no employee ID or department was recorded.
The chair circulated the minutes without an attendance sheet.
The approval concerns door schedules and conveys no procurement spending authority.
A corrected attendee list was requested but is not present in this archive.
""",
    "people/technical_review.txt": """Review CHG-311 | signed 2025-04-04
Alex Kim, employee E-208, approved the connection-pool monitoring change.
The platform engineering reviewer requested an alert on sustained queue growth.
Deployment is scheduled after the synthetic load test; this is not the loading-bay review.
No change to warehouse access permissions is included in CHG-311.
""",
    "multilingual/order_de.txt": """Betreff: Bestellung PO-608 / Projekt Linden
Datum: 2025-05-06; Absenderin: Eva Sommer, Einkauf
Die Lieferung umfasst 24 Pumpen für das Werk Süd.
Der bestätigte Liefertermin ist der 19. Mai 2025.
Die ursprünglich vorgeschlagene Lieferung am 12. Mai ist damit ersetzt.
Bitte die Seriennummern vor dem Versand an die Qualitätsstelle melden.
""",
    "multilingual/inspection_fr.txt": """Compte rendu de réception PO-608 | 20 mai 2025
Site : usine Sud ; responsable : Luc Martin.
Sur les 24 pompes reçues, deux présentent une fuite et restent en quarantaine.
Les 22 autres pompes sont acceptées pour installation.
Le fournisseur doit proposer une date de remplacement ; aucune date n'est confirmée.
Les photographies sont conservées par le service qualité, hors de ce dossier.
""",
    "multilingual/replacement_es.txt": """Asunto: PO-608, propuesta de sustitución | 21 de mayo de 2025
Podemos enviar dos bombas de sustitución el 27 de mayo, sujeto a confirmación del cliente.
Esta propuesta no constituye una fecha de entrega confirmada.
Por favor, mantengan las unidades defectuosas separadas del material instalado.
El equipo comercial espera la respuesta de la planta Sur antes de reservar transporte.
""",
    "governance/risk_register.md": """# Project Alder risk register | reviewed 2025-06-09
R-12: supplier exit risk; owner Farah Singh; mitigation is a tested export procedure.
R-15: late training; owner Tomas Reed; mitigation is an additional workshop.
R-12 is open until an export can be restored into the replacement environment.
A successful download alone does not meet the closure criterion.
Next review: 2025-06-23. Risk owners may propose but not self-approve closure.
""",
    "governance/risk_test.txt": """Alder continuity exercise EX-14 | 2025-06-16
An archive was downloaded and its checksum matched the source manifest.
Restore into the replacement environment failed because the schema version was unsupported.
R-12 remains open; Farah Singh will request a compatible importer.
The exercise used synthetic records and made no production changes.
The next run must include row counts and a read test after restoration.
""",
    "contracts/service.md": """# Service schedule S-22: Harbor analytics
Effective 2025-07-01; countersigned by both parties on 2025-06-24.
Service credits apply only when monthly availability falls below 99.5%.
Planned maintenance announced at least 72 hours in advance is excluded from availability.
Credit claims must arrive within 15 calendar days after the affected month ends.
This schedule provides no automatic refund and does not cover customer network outages.
""",
    "contracts/maintenance.txt": """Harbor maintenance notice M-18 | sent 2025-07-07 08:00 UTC
Planned start: 2025-07-10 10:00 UTC; planned duration: 45 minutes.
The notice concerns index maintenance under service schedule S-22.
Operators must record the actual start and end; this notice alone proves no downtime.
Recipients: customer operations and service desk distribution lists.
""",
    "finance/forecast.md": """# Project Alder cash forecast | draft 2025-06-18
Finance modeled a potential EUR 18,000 rebate if the annual volume threshold is reached.
No signed rebate agreement or achieved annual volume is included in this archive.
Do not recognize the modeled rebate as a receivable.
The forecast is for planning only; it is not a vendor commitment or payment record.
""",
    "archive/coverage.txt": """Archive handover note | fictional exercise collection
Included: procurement decisions, incident notes, policies, directory extracts and project records.
Missing: INC-73 database samples and payment-provider logs; PO-608 replacement acceptance.
The archive contains no PR-417 bank transfer confirmation and no Alder signed rebate agreement.
Absence from this collection does not establish that an event never happened.
Use the source-specific status statements and distinguish proposals from executed decisions.
""",
}


def _case(cid, question, category, answer, evidence, answerable=True):
    return {
        "id": cid,
        "question": question,
        "category": category,
        "expected_answer": answer,
        "required_evidence": [{"path": p, "quote": q} for p, q in evidence],
        "answerable": answerable,
    }


def _document_cases():
    return [
        _case(
            "doc-01",
            "For PR-417, what quantity and spending ceiling applied on 14 February 2025?",
            "temporal_approval",
            "100 sensors and EUR 42,000 excluding VAT; A1 was not effective until 15 February.",
            [
                (
                    "procurement/02_approval.txt",
                    "Approved ceiling: EUR 42,000 excluding VAT for 100 sensors.",
                ),
                (
                    "procurement/03_amendment.md",
                    "A1 takes effect on 2025-02-15; all other FIN-82 terms remain unchanged.",
                ),
            ],
        ),
        _case(
            "doc-02",
            "What was PR-417's authorized ceiling and quantity on 16 February, and who approved the increase?",
            "amendment",
            "Lena Ortiz approved EUR 46,200 excluding VAT for 110 sensors, effective 15 February.",
            [
                (
                    "procurement/03_amendment.md",
                    "Signed: Lena Ortiz, 2025-02-12; acknowledged by Vale Instruments on 2025-02-13.",
                ),
                (
                    "procurement/03_amendment.md",
                    "A1 increases the approved ceiling to EUR 46,200 excluding VAT for 110 sensors.",
                ),
                (
                    "procurement/03_amendment.md",
                    "A1 takes effect on 2025-02-15; all other FIN-82 terms remain unchanged.",
                ),
            ],
        ),
        _case(
            "doc-03",
            "Did the supplier email authorize 120 sensors for PR-417?",
            "conflicting_request",
            "No; 120 was a preference, not an approved quantity, and the email did not amend approval.",
            [
                (
                    "procurement/04_supplier_email.txt",
                    "The annex team would prefer 120 units, but that is not an approved quantity.",
                ),
                (
                    "procurement/04_supplier_email.txt",
                    "This email does not amend FIN-82 or A1.",
                ),
            ],
        ),
        _case(
            "doc-04",
            "How many PR-417 sensors entered service and what invoice amount was disputed?",
            "reconciliation",
            "107 entered service; EUR 1,260 excluding VAT was disputed for the 3 rejected sensors.",
            [
                (
                    "procurement/05_receipt.txt",
                    "Accepted into service: 107 sensors. Replacement of the 3 units remains open.",
                ),
                (
                    "procurement/06_invoice.md",
                    "The unit price is EUR 420; the disputed amount for 3 rejected sensors is EUR 1,260.",
                ),
            ],
        ),
        _case(
            "doc-05",
            "How long did INC-73 degradation last, and did rollback restore service?",
            "incident_timeline",
            "44 minutes (08:12–08:56 UTC); rollback completed at 08:39 without recovery, which followed the 08:47 pool-limit increase.",
            [
                (
                    "incident/01_alert.txt",
                    "08:12 — Monitor detected elevated checkout errors in the Birch region.",
                ),
                (
                    "incident/02_bridge.txt",
                    "08:39 — Rollback completed; errors remained above baseline.\n08:47 — Connection pool limit increased from 40 to 80.\n08:56 — Error rate returned to baseline and stayed there for the observation window.",
                ),
            ],
        ),
        _case(
            "doc-06",
            "What was the confirmed root cause of INC-73?",
            "unanswerable_causality",
            "Cannot establish a confirmed cause: sampling was disabled; recovery after mitigation does not establish causality.",
            [
                (
                    "incident/03_review.md",
                    "Root cause remains unconfirmed because database sampling was disabled during the event.",
                )
            ],
            False,
        ),
        _case(
            "doc-07",
            "As of 3 March 2025, what approved retention applies to diagnostic logs under RP-9?",
            "version_conflict",
            "30 days under version 2; version 1 is superseded and version 3 is only a draft.",
            [
                (
                    "versions/retention_v2.md",
                    "Diagnostic logs are retained for 30 days; this supersedes version 1.",
                ),
                (
                    "versions/retention_draft.md",
                    "The privacy council has not voted; version 2 remains the approved policy.",
                ),
            ],
        ),
        _case(
            "doc-08",
            "Which employee ID belongs to the A. Kim who approved the loading-bay change?",
            "ambiguous_identity",
            "Cannot disambiguate E-104 and E-208: the minutes contain no employee ID or department.",
            [
                (
                    "people/directory.txt",
                    "Alex Kim (employee E-104) works in procurement, based in Dublin.\nAlex Kim (employee E-208) works in platform engineering, based in Oslo.",
                ),
                (
                    "people/signoff.txt",
                    "A. Kim approved the loading-bay access change; no employee ID or department was recorded.",
                ),
            ],
            False,
        ),
        _case(
            "doc-09",
            "Which Alex Kim approved CHG-311 and in what department?",
            "identity_resolution",
            "Alex Kim E-208 in platform engineering.",
            [
                (
                    "people/technical_review.txt",
                    "Alex Kim, employee E-208, approved the connection-pool monitoring change.",
                ),
                (
                    "people/directory.txt",
                    "Alex Kim (employee E-208) works in platform engineering, based in Oslo.",
                ),
            ],
        ),
        _case(
            "doc-10",
            "For PO-608, what delivery date replaced the original proposal and how many pumps were accepted? Answer in English.",
            "multilingual_join",
            "19 May 2025 replaced 12 May; 22 of 24 pumps were accepted and two quarantined.",
            [
                (
                    "multilingual/order_de.txt",
                    "Der bestätigte Liefertermin ist der 19. Mai 2025.\nDie ursprünglich vorgeschlagene Lieferung am 12. Mai ist damit ersetzt.",
                ),
                (
                    "multilingual/inspection_fr.txt",
                    "Sur les 24 pompes reçues, deux présentent une fuite et restent en quarantaine.\nLes 22 autres pompes sont acceptées pour installation.",
                ),
            ],
        ),
        _case(
            "doc-11",
            "What is the confirmed delivery date for PO-608 replacement pumps?",
            "unanswerable_proposal",
            "No confirmed delivery date is recorded; 27 May is a proposed shipment date subject to customer confirmation.",
            [
                (
                    "multilingual/replacement_es.txt",
                    "Podemos enviar dos bombas de sustitución el 27 de mayo, sujeto a confirmación del cliente.\nEsta propuesta no constituye una fecha de entrega confirmada.",
                )
            ],
            False,
        ),
        _case(
            "doc-12",
            "Should Alder risk R-12 be closed after EX-14, and who owns the next action?",
            "closure_criteria",
            "No: restoration failed despite checksum verification; Farah Singh must request a compatible importer.",
            [
                (
                    "governance/risk_register.md",
                    "R-12 is open until an export can be restored into the replacement environment.",
                ),
                (
                    "governance/risk_test.txt",
                    "Restore into the replacement environment failed because the schema version was unsupported.\nR-12 remains open; Farah Singh will request a compatible importer.",
                ),
            ],
        ),
        _case(
            "doc-13",
            "Was Harbor M-18 announced early enough for the S-22 planned-maintenance exclusion?",
            "contract_time_arithmetic",
            "Yes: 74 hours' notice exceeds the 72-hour requirement; actual downtime is not established by the notice.",
            [
                (
                    "contracts/service.md",
                    "Planned maintenance announced at least 72 hours in advance is excluded from availability.",
                ),
                (
                    "contracts/maintenance.txt",
                    "Harbor maintenance notice M-18 | sent 2025-07-07 08:00 UTC\nPlanned start: 2025-07-10 10:00 UTC; planned duration: 45 minutes.",
                ),
            ],
        ),
        _case(
            "doc-14",
            "On what date was the PR-417 invoice paid?",
            "unanswerable_missing_record",
            "Cannot determine payment date; no payment release or bank transfer confirmation is in the collection.",
            [
                (
                    "procurement/06_invoice.md",
                    "No payment release is recorded in this reconciliation note.",
                ),
                (
                    "archive/coverage.txt",
                    "The archive contains no PR-417 bank transfer confirmation and no Alder signed rebate agreement.",
                ),
            ],
            False,
        ),
        _case(
            "doc-15",
            "Can finance recognize the EUR 18,000 Alder rebate as a receivable from this archive?",
            "forecast_vs_commitment",
            "No; it is a conditional forecast without a signed agreement or evidence that the volume threshold was achieved.",
            [
                (
                    "finance/forecast.md",
                    "No signed rebate agreement or achieved annual volume is included in this archive.\nDo not recognize the modeled rebate as a receivable.",
                )
            ],
        ),
        _case(
            "doc-16",
            "Does RP-9 establish the retention period for financial ledgers?",
            "scope_boundary",
            "No; financial ledger retention is outside RP-9, and no ledger retention period is established here.",
            [
                (
                    "versions/retention_v2.md",
                    "Financial ledger retention is outside the scope of RP-9.",
                )
            ],
        ),
    ]


def _empty_destination(destination):
    destination = Path(destination)
    if destination.is_symlink() or (
        destination.exists()
        and (not destination.is_dir() or any(destination.iterdir()))
    ):
        raise ValueError("destination must be absent or an empty directory")
    destination.mkdir(parents=True, exist_ok=True)
    return destination


def _validate(cases, texts):
    for case in cases:
        for evidence in case["required_evidence"]:
            if not evidence["quote"] or evidence["quote"] not in texts.get(
                evidence["path"], ""
            ):
                raise ValueError(
                    f"Gold evidence does not match exported source: {case['id']} {evidence['path']}"
                )


def _distractor(index):
    """Varied routine business records with explicit unrelated project identifiers."""
    day = date(2024, 1, 1) + timedelta(days=index % 540)
    site = ("East", "West", "North", "South", "Annex", "Central")[index % 6]
    project = ("Juniper", "Cedar", "Maple", "Willow", "Aspen", "Elm", "Pine")[index % 7]
    owner = ("Iris Bell", "Omar Vale", "Mei Rivers", "Jonas Berg", "Sara Holt")[
        index % 5
    ]
    units = 12 + (index * 17) % 180
    amount = units * (75 + index % 60)
    record = f"OPS-{index:05d}"
    common = f"Record: {record}; project: {project}-{index // 7:03d}; site: {site}\nDate: {day.isoformat()}; owner: {owner}\n"
    bodies = [
        f"# Purchasing review\n{common}Requested {units} replacement filter housings for EUR {amount:,} excluding VAT.\nThe supplier quote excludes installation; facilities must check mounting dimensions.\nStatus: awaiting cost-centre approval; this minute is not a purchase order.\nA sample will be inspected before the bulk shipment is booked.\nNext action: obtain a second quote and reconcile freight assumptions.\n",
        f"# Shift handover\n{common}The afternoon team processed {units} return cartons and separated two with damaged seals.\nThe label printer paused during a ribbon change; the queue was replayed without duplicate labels.\nNo customer delivery commitment was changed during this shift.\nKeep the quarantine cage locked until quality reviews the receiving photographs.\nNext shift should compare the physical count with the warehouse ledger.\n",
        f"# Change readiness review\n{common}Change window requested for the staging telemetry collector; {units} endpoints are in scope.\nA dry run preserved the previous configuration and produced a rollback archive.\nThe review found an unsigned operational checklist, so deployment approval remains pending.\nAcceptance requires a successful restart plus an alert-routing exercise.\nThe production window will be considered only after the service owner signs.\n",
        f"# Supplier service review\n{common}The service desk closed {units} tickets in the reporting interval.\nTwo cases were reopened because replacement part numbers did not match the installed assembly.\nA service credit of EUR {amount:,} is a discussion item and has not been agreed.\nThe account team requested a corrective-action schedule with named owners.\nKeep the draft meeting slides separate from the signed service schedule.\n",
        f"# Schulungsprotokoll\n{common}Für die Unterweisung wurden {units} Plätze reserviert.\nDie neue Prüfliste wurde mit einem Musterauftrag getestet; zwei Feldnamen waren unklar.\nDie Freigabe bleibt bis zur Korrektur der Übersetzung offen.\nDie Standortleitung prüft den Entwurf in der nächsten Sitzung.\nDies ist ein interner Arbeitsstand und keine Änderung der Lieferbedingungen.\n",
        f"# Compte rendu qualité\n{common}Le contrôle porte sur un lot de {units} pièces de rechange.\nLes mesures dimensionnelles sont conformes, mais le certificat matière manque au dossier.\nLe lot reste bloqué en attendant le certificat signé du fournisseur.\nLe responsable demande une copie lisible et la référence du lot d'origine.\nAucune autorisation de mise en service n'est donnée par ce compte rendu.\n",
        f"# Budget variance memo\n{common}The working forecast allocates EUR {amount:,} to {units} reusable transport crates.\nThe actual ledger is not closed; a late freight invoice could change the variance.\nOperations asked to shift spend into next quarter rather than reduce inspection coverage.\nFinance has requested evidence of receipt before accruing the supplier charge.\nThe draft does not approve a new budget or authorize payment.\n",
        f"# Maintenance work order\n{common}Technicians inspected {units} valve labels during the scheduled access window.\nOne asset tag was unreadable, so its identity is recorded as unresolved.\nThe inspection found no active leak; replacement seals remain on the next service plan.\nReopening the line requires the shift supervisor's signoff and a pressure check.\nPhotographs are referenced in the work order but are outside this text extract.\n",
    ]
    return LABEL + "\n\n" + bodies[index % len(bodies)]


def build_documents(destination: Path, count: int = 1000) -> list[dict]:
    """Write exactly count UTF-8 documents; count must be an integer >= 30.

    Returns the same fixed case suite for every valid count. Refuses nonempty
    destinations rather than deleting existing files. No gold is written there.
    """
    if type(count) is not int or count < 30:
        raise ValueError("count must be an integer >= 30")
    texts = {path: LABEL + "\n\n" + body for path, body in DOCUMENTS.items()}
    for index in range(count - len(texts)):
        texts[
            f"operations/{index // 100:03d}/record_{index:05d}.{'md' if index % 2 else 'txt'}"
        ] = _distractor(index)
    cases = _document_cases()
    _validate(cases, texts)
    destination = _empty_destination(destination)
    for path, body in sorted(texts.items()):
        target = destination / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body.encode("utf-8"))
    return cases


_SOURCE_SUFFIXES = {
    ".py",
    ".pyi",
    ".md",
    ".txt",
    ".rst",
    ".adoc",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".css",
    ".scss",
    ".html",
    ".sql",
    ".sh",
    ".bash",
    ".zsh",
    ".toml",
    ".yaml",
    ".yml",
    ".json",
    ".xml",
    ".ini",
    ".cfg",
    ".conf",
    ".c",
    ".h",
    ".cpp",
    ".hpp",
    ".rs",
    ".go",
    ".java",
    ".swift",
    ".dart",
}
_EXCLUDED_PARTS = {
    ".git",
    ".agents",
    ".codex",
    ".serena",
    ".swarm",
    "node_modules",
    "vendor",
    "artifacts",
    "artifact",
    "dist",
    "build",
    "target",
    "coverage",
    "__pycache__",
    "private",
    "secrets",
    "credentials",
    "customer",
    "customers",
    "customer_data",
    "private_data",
    "raw_data",
    "datasets",
    "corpora",
    "runs",
    "results",
    ".venv",
}


def _exportable(path):
    path = PurePosixPath(path)
    parts = [part.lower() for part in path.parts]
    name = parts[-1]
    if any(part in _EXCLUDED_PARTS or "benchmark" in part for part in parts):
        return False
    if any(
        token in name
        for token in (".lock", "-lock.", "secret", "credential", "private")
    ):
        return False
    if name.startswith(".env") or name in {
        "id_rsa",
        "id_ed25519",
        "npm-shrinkwrap.json",
    }:
        return False
    return path.suffix.lower() in _SOURCE_SUFFIXES or name in {
        "license",
        "copying",
        "notice",
        "dockerfile",
        "makefile",
        "justfile",
        "dev",
        ".gitignore",
    }


def _code_cases():
    prefix = "evidencekg/src/evidencekg/"

    def evidence(module, quote):
        return (prefix + module + ".txt", quote)

    return [
        _case(
            "code-01",
            "How does ingestion freeze lexical search to a snapshot rather than query the mutable global segment index?",
            "cross_module_snapshot_retrieval",
            "Ingestion calls freeze_fts; it copies segments belonging to snapshot_documents into a snapshot-specific FTS table, and lexical search obtains that table through fts_name.",
            [
                evidence("ingest.py", "freeze_fts(store, snapshot)"),
                evidence(
                    "snapshots.py",
                    "INSERT INTO {name} SELECT s.id,s.text FROM segments s JOIN snapshot_documents sd ON sd.extraction_id=s.extraction_id WHERE sd.snapshot_id=? ORDER BY s.id",
                ),
                evidence("retrieval.py", "name = fts_name(snapshot_id)"),
            ],
        ),
        _case(
            "code-02",
            "What happens if a retrieval page cursor is reused with another snapshot or query scope?",
            "cursor_failure",
            "The decoded cursor snapshot and scope hash must match; mismatch is caught and surfaced as ValueError('Invalid cursor').",
            [
                evidence(
                    "retrieval.py",
                    'if value["snapshot"] != snapshot or value["scope"] != signature:\n                    raise ValueError("Cursor scope/snapshot mismatch")',
                ),
                evidence(
                    "retrieval.py",
                    'except Exception as exc:\n                raise ValueError("Invalid cursor") from exc',
                ),
            ],
        ),
        _case(
            "code-03",
            "How does a parser timeout affect its process group and the ingestion result?",
            "cross_module_worker_failure",
            "Communication is bounded by parser_timeout; an exception kills the process group and waits for the parser child. Timeout becomes an empty failed result with a warning, whose status ingestion records.",
            [
                evidence(
                    "parsers/__init__.py",
                    'stdout, stderr = p.communicate(timeout=cfg["parser_timeout"])',
                ),
                evidence(
                    "parsers/__init__.py",
                    "except BaseException:\n                os.killpg(p.pid, signal.SIGKILL)\n                p.wait()\n                raise",
                ),
                evidence(
                    "parsers/__init__.py",
                    'except (OSError, ValueError, subprocess.TimeoutExpired) as exc:\n            return dict(sections=[], warnings=[str(exc)], attachments=[], artifacts=[], status="failed")',
                ),
                evidence(
                    "ingest.py",
                    'status = result["status"]\n                    warnings += result["warnings"]',
                ),
            ],
        ),
        _case(
            "code-04",
            "Why does failed PDF extraction affect whether inventory can report a complete total?",
            "cross_module_completeness",
            "A failed PDF may hide descendants, so ingestion marks inventory incomplete; inventory paging then returns total=None while retaining known_matches.",
            [
                evidence(
                    "ingest.py",
                    'if status == "failed" and (\n                Path(path).suffix.lower() in (".eml", ".pdf", ".docx") or mime == "message/rfc822"\n            ):\n                unknown_descendants = True',
                ),
                evidence("ingest.py", 'manifest["inventory_complete"] = False'),
                evidence(
                    "retrieval.py",
                    'total=None if scope["kind"] == "inventory" and not manifest["inventory_complete"] else total,\n            known_matches=total,',
                ),
            ],
        ),
        _case(
            "code-05",
            "Can citation-choice resolution silently repair an altered quote or accept an unknown citation ID?",
            "cross_module_citation_validation",
            "No. Choices validates source references and exact substrings; altered quotations fail, and resolution rejects unknown IDs or objects containing extra fields.",
            [
                evidence("citation_choices.py", "validate_refs([ref], segments)"),
                evidence(
                    "validation.py",
                    'if seg["text"][lo:hi] != ref["quote"]:\n            raise ValueError("Fabricated or altered quotation")',
                ),
                evidence(
                    "citation_choices.py",
                    'if set(node) != {"citation_id"} or node["citation_id"] not in catalog:\n                    raise ValueError("Unknown or malformed citation choice")',
                ),
            ],
        ),
        _case(
            "code-06",
            "How are citation IDs tied to immutable spans and duplicate choices rejected?",
            "citation_identity",
            "Each ID must equal ident('Q', segment ID, start, end); source refs are validated and duplicate IDs raise ValueError.",
            [
                evidence(
                    "citation_choices.py",
                    'if cid != ident("Q", segment["id"], ref["start"], ref["end"]) or cid in result:\n                raise ValueError("Invalid or duplicate citation choice")',
                )
            ],
        ),
        _case(
            "code-07",
            "When a prior extraction failed, does a later ingest simply reuse it?",
            "ingestion_retry",
            "No: failed status or reextract creates a retry extraction identity containing a fresh UUID and clears the existing result so parsing runs again.",
            [
                evidence(
                    "ingest.py",
                    'if existing and (existing["status"] == "failed" or reextract):\n                    extraction = ident("X", extraction, "retry", uuid.uuid4().hex)\n                    existing = None',
                )
            ],
        ),
        _case(
            "code-08",
            "How does the default snapshot read follow the acquisition head, and where is that head updated?",
            "cross_module_head",
            "Store.snapshot(None) reads current_snapshot first, falling back to the latest snapshot if no head exists; ingestion upserts current_snapshot after freezing FTS.",
            [
                evidence(
                    "db.py",
                    'head = self.db.execute("SELECT snapshot_id FROM current_snapshot WHERE singleton=1").fetchone()',
                ),
                evidence(
                    "db.py",
                    'return self.one("SELECT * FROM snapshots ORDER BY created_at DESC,id DESC LIMIT 1")',
                ),
                evidence(
                    "ingest.py",
                    "INSERT INTO current_snapshot VALUES(1,?) ON CONFLICT(singleton) DO UPDATE SET snapshot_id=excluded.snapshot_id",
                ),
            ],
        ),
        _case(
            "code-09",
            "What happens when a single paginated retrieval result exceeds the response byte budget?",
            "retrieval_budget_failure",
            "The budget is 750,000 encoded JSON bytes. An oversized first result raises ValueError directing the caller to bounded source/feature reads; after existing items it ends the page.",
            [
                evidence(
                    "retrieval.py",
                    'if used + size > 750000:\n                if not items:\n                    raise ValueError("One result exceeds response budget; use bounded source/feature reads")\n                break',
                )
            ],
        ),
        _case(
            "code-10",
            "Can a parser continue if its Linux network-denial policy cannot be loaded?",
            "worker_isolation_failure",
            "The network policy raises RuntimeError when seccomp load fails and releases the context in finally; it does not silently continue without the policy.",
            [
                evidence(
                    "parsers/isolation.py",
                    'if library.seccomp_load(context):\n            raise RuntimeError("Cannot apply parser network policy")\n    finally:\n        library.seccomp_release(context)',
                )
            ],
        ),
    ]


def build_code(destination: Path, repository: Path) -> list[dict]:
    """Export source/docs from one captured Git HEAD, never dirty worktree files.

    Tracked regular UTF-8 source/doc/config files are exported; binaries, symlinks,
    benchmark material, generated artifacts, lockfiles and private-named paths
    are excluded. Unsupported textual formats acquire an additional .txt suffix.
    Gold is validated before writing, so an incompatible HEAD fails explicitly.
    """
    repository = Path(repository)

    def git(*args):
        return subprocess.run(
            ["git", "-C", str(repository), *args], check=True, capture_output=True
        ).stdout

    revision = git("rev-parse", "HEAD").decode("ascii").strip()
    texts = {}
    # NUL-delimited tree records preserve whitespace and newlines in paths.
    for entry in git("ls-tree", "-r", "-z", revision).split(b"\0"):
        if not entry:
            continue
        metadata, raw_path = entry.split(b"\t", 1)
        mode, kind, _ = metadata.split()
        path = raw_path.decode("utf-8")
        if (
            kind != b"blob"
            or mode not in (b"100644", b"100755")
            or not _exportable(path)
        ):
            continue
        raw = git("show", f"{revision}:{path}")
        if b"\0" in raw:
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue
        output = (
            path if PurePosixPath(path).suffix in {".md", ".txt"} else path + ".txt"
        )
        if output in texts:
            raise ValueError(f"Export path collision: {output}")
        texts[output] = text
    cases = _code_cases()
    _validate(cases, texts)
    destination = _empty_destination(destination)
    for path, text in sorted(texts.items()):
        target = destination / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(text.encode("utf-8"))
    return cases
