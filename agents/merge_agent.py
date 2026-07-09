import uuid
from datetime import datetime, timezone
from typing import Dict, Any, List

from agents.base import BaseAgent, AgentValidationError
from audit.audit import log_audit, log_evidence
from db.connection import transaction

class MergeAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            name="MergeAgent",
            version="1.0.0",
            role="Consolidates individual shareholding disclosures into unified, audited quarterly snapshots"
        )

    def validate_inputs(self, inputs: dict):
        super().validate_inputs(inputs)
        if "execution_id" not in inputs:
            raise AgentValidationError("Missing required input field: 'execution_id'")

    def _get_active_companies(self) -> List[Dict[str, str]]:
        active_companies = []
        with transaction() as conn:
            rows = conn.execute(
                "SELECT company_uuid, symbol, name FROM companies WHERE status = 'Active'"
            ).fetchall()
            for row in rows:
                active_companies.append({
                    "company_uuid": row["company_uuid"],
                    "symbol": row["symbol"],
                    "name": row["name"]
                })
        return active_companies

    def _get_target_quarter(self, inputs: dict) -> str:
        pipeline_state = inputs.get("pipeline_state", {})
        initial_inputs = pipeline_state.get("initial_inputs", {})
        return initial_inputs.get("quarter", "2026-Q2")

    def execute(self, inputs: dict, job_id: str) -> dict:
        execution_id = inputs["execution_id"]
        quarter = self._get_target_quarter(inputs)
        companies = self._get_active_companies()

        log_audit(job_id, "Fetch", f"Consolidating ownership snapshots for {len(companies)} active companies in {quarter}")
        timestamp_now = datetime.now(timezone.utc).isoformat()

        snapshots_saved = 0
        with transaction() as conn:
            for comp in companies:
                company_uuid = comp["company_uuid"]
                symbol = comp["symbol"]

                # 1. Fetch raw holding entries for target quarter
                p_row = conn.execute(
                    "SELECT promoter_holding_pct FROM promoter_holdings WHERE company_uuid = ? AND quarter = ? AND is_current = 1",
                    (company_uuid, quarter)
                ).fetchone()

                f_row = conn.execute(
                    "SELECT fii_holding_pct FROM fii_holdings WHERE company_uuid = ? AND quarter = ? AND is_current = 1",
                    (company_uuid, quarter)
                ).fetchone()

                d_row = conn.execute(
                    "SELECT dii_holding_pct FROM dii_holdings WHERE company_uuid = ? AND quarter = ? AND is_current = 1",
                    (company_uuid, quarter)
                ).fetchone()

                m_row = conn.execute(
                    "SELECT mf_holding_pct FROM mutual_fund_holdings WHERE company_uuid = ? AND quarter = ? AND is_current = 1",
                    (company_uuid, quarter)
                ).fetchone()

                pub_row = conn.execute(
                    "SELECT public_holding_pct FROM public_holdings WHERE company_uuid = ? AND quarter = ? AND is_current = 1",
                    (company_uuid, quarter)
                ).fetchone()

                pledge_row = conn.execute(
                    "SELECT pledged_pct FROM promoter_pledges WHERE company_uuid = ? AND quarter = ? AND is_current = 1",
                    (company_uuid, quarter)
                ).fetchone()

                # 2. Check for missing values
                if not (p_row and f_row and d_row and m_row and pub_row and pledge_row):
                    missing_sources = []
                    if not p_row: missing_sources.append("Promoter")
                    if not f_row: missing_sources.append("FII")
                    if not d_row: missing_sources.append("DII")
                    if not m_row: missing_sources.append("Mutual Fund")
                    if not pub_row: missing_sources.append("Public")
                    if not pledge_row: missing_sources.append("Pledge")
                    
                    raise AgentValidationError(
                        f"Consolidation failed for {symbol} in {quarter}: Missing raw data from {', '.join(missing_sources)}"
                    )

                promoter = p_row["promoter_holding_pct"]
                fii = f_row["fii_holding_pct"]
                dii = d_row["dii_holding_pct"]
                mf = m_row["mf_holding_pct"]
                public = pub_row["public_holding_pct"]
                pledge = pledge_row["pledged_pct"]

                # 3. Sum Validation (Promoter + FII + DII + Public should equal 100%)
                total_sum = promoter + fii + dii + public
                if not (99.0 <= total_sum <= 101.0):
                    raise AgentValidationError(
                        f"Sum check failed for {symbol} in {quarter}: Holdings sum is {total_sum:.2f}% (Expected ~100.0%)"
                    )

                # 4. Check existing snapshot versions
                last_snap = conn.execute(
                    "SELECT version FROM ownership_snapshots WHERE company_uuid = ? AND quarter = ? ORDER BY version DESC LIMIT 1",
                    (company_uuid, quarter)
                ).fetchone()

                if last_snap:
                    try:
                        next_version = str(int(last_snap["version"]) + 1)
                    except ValueError:
                        next_version = "2"
                else:
                    next_version = "1"

                # 5. Archive previous snapshots
                conn.execute(
                    "UPDATE ownership_snapshots SET is_current = 0 WHERE company_uuid = ? AND quarter = ?",
                    (company_uuid, quarter)
                )

                # 6. Insert new merged snapshot
                snapshot_uuid = str(uuid.uuid4())
                conn.execute(
                    """
                    INSERT INTO ownership_snapshots (
                        snapshot_uuid, company_uuid, quarter, version, is_current,
                        promoter_holding_pct, fii_holding_pct, dii_holding_pct, mf_holding_pct,
                        public_holding_pct, pledged_pct, created_at, execution_id, source
                    )
                    VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, 'Consolidated Exchange Disclosures')
                    """,
                    (
                        snapshot_uuid, company_uuid, quarter, next_version,
                        promoter, fii, dii, mf, public, pledge,
                        timestamp_now, execution_id
                    )
                )

                # Log evidence on version 1 snapshot creation
                if next_version == "1":
                    log_evidence(
                        job_id=job_id,
                        company_uuid=company_uuid,
                        field_name="ownership_merged",
                        value=f"Merged snapshot for {quarter}: Promoter={promoter}%, FII={fii}%, DII={dii}%, Public={public}%",
                        source="Ownership Consolidator",
                        confidence_score=1.0,
                        quarter=quarter,
                        conn=conn
                    )
                snapshots_saved += 1

        log_audit(
            job_id=job_id,
            step="Completion",
            action="Ownership merge completed",
            metadata={"snapshots_merged": snapshots_saved}
        )

        return {
            "status": "success",
            "quarter": quarter,
            "snapshots_merged_count": snapshots_saved,
            "metrics": {
                "records_processed": snapshots_saved
            }
        }
