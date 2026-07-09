import json
import uuid
from datetime import datetime, timezone
from typing import Dict, Any, List

from agents.base import BaseAgent, AgentValidationError
from audit.audit import log_audit, log_evidence
from db.connection import transaction

class AnalyticsAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            name="AnalyticsAgent",
            version="1.0.0",
            role="Calculates quarter-over-quarter ownership changes and identifies top institutional trends"
        )

    def validate_inputs(self, inputs: dict):
        super().validate_inputs(inputs)
        if "execution_id" not in inputs:
            raise AgentValidationError("Missing required input field: 'execution_id'")

    def _get_target_quarter(self, inputs: dict) -> str:
        pipeline_state = inputs.get("pipeline_state", {})
        initial_inputs = pipeline_state.get("initial_inputs", {})
        return initial_inputs.get("quarter", "2026-Q2")

    def _get_previous_quarter(self, q: str) -> str:
        year, qtr = q.split("-")
        q_num = int(qtr[1])
        if q_num == 1:
            return f"{int(year)-1}-Q4"
        else:
            return f"{year}-Q{q_num-1}"

    def execute(self, inputs: dict, job_id: str) -> dict:
        execution_id = inputs["execution_id"]
        quarter = self._get_target_quarter(inputs)
        prev_quarter = self._get_previous_quarter(quarter)

        log_audit(job_id, "Process", f"Calculating ownership deltas for {quarter} (baseline: {prev_quarter})")
        timestamp_now = datetime.now(timezone.utc).isoformat()

        # 1. Fetch active companies
        companies = []
        with transaction() as conn:
            rows = conn.execute("SELECT company_uuid, symbol, name FROM companies WHERE status = 'Active'").fetchall()
            for r in rows:
                companies.append(dict(r))

        analytics_records = []
        
        with transaction() as conn:
            for comp in companies:
                company_uuid = comp["company_uuid"]
                symbol = comp["symbol"]

                # Fetch current snapshot (Q2)
                q2_row = conn.execute(
                    """
                    SELECT promoter_holding_pct, fii_holding_pct, dii_holding_pct, mf_holding_pct, pledged_pct 
                    FROM ownership_snapshots 
                    WHERE company_uuid = ? AND quarter = ? AND is_current = 1
                    """,
                    (company_uuid, quarter)
                ).fetchone()

                # Fetch previous snapshot (Q1)
                q1_row = conn.execute(
                    """
                    SELECT promoter_holding_pct, fii_holding_pct, dii_holding_pct, mf_holding_pct, pledged_pct 
                    FROM ownership_snapshots 
                    WHERE company_uuid = ? AND quarter = ? AND is_current = 1
                    """,
                    (company_uuid, prev_quarter)
                ).fetchone()

                if q2_row:
                    if q1_row:
                        prom_delta = round(q2_row["promoter_holding_pct"] - q1_row["promoter_holding_pct"], 2)
                        fii_delta = round(q2_row["fii_holding_pct"] - q1_row["fii_holding_pct"], 2)
                        dii_delta = round(q2_row["dii_holding_pct"] - q1_row["dii_holding_pct"], 2)
                        mf_delta = round(q2_row["mf_holding_pct"] - q1_row["mf_holding_pct"], 2)
                        pledge_delta = round(q2_row["pledged_pct"] - q1_row["pledged_pct"], 2)
                    else:
                        prom_delta = 0.0
                        fii_delta = 0.0
                        dii_delta = 0.0
                        mf_delta = 0.0
                        pledge_delta = 0.0

                    # Deprecate old calculations
                    conn.execute(
                        "UPDATE company_quarterly_analytics SET is_current = 0 WHERE company_uuid = ? AND quarter = ?",
                        (company_uuid, quarter)
                    )

                    # Insert new delta computation
                    conn.execute(
                        """
                        INSERT INTO company_quarterly_analytics (
                            company_uuid, quarter, promoter_delta, fii_delta, dii_delta, mf_delta, pledged_delta,
                            is_current, created_at, execution_id, source
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, 'Analytics Processor')
                        """,
                        (company_uuid, quarter, prom_delta, fii_delta, dii_delta, mf_delta, pledge_delta, timestamp_now, execution_id)
                    )

                    analytics_records.append({
                        "symbol": symbol,
                        "name": comp["name"],
                        "promoter_delta": prom_delta,
                        "fii_delta": fii_delta,
                        "dii_delta": dii_delta,
                        "mf_delta": mf_delta,
                        "pledged_delta": pledge_delta
                    })

        # 2. Compile Top Rankings
        # Sort criteria: Promoter Delta (DESC), then FII Delta (DESC), then DII Delta (DESC)
        top_combined = sorted(
            analytics_records,
            key=lambda x: (x["promoter_delta"], x["fii_delta"], x["dii_delta"]),
            reverse=True
        )[:10]

        # Top FII Buying (fii_delta DESC)
        top_fii = sorted(analytics_records, key=lambda x: x["fii_delta"], reverse=True)[:10]
        # Top Promoter Buying (promoter_delta DESC)
        top_prom = sorted(analytics_records, key=lambda x: x["promoter_delta"], reverse=True)[:10]
        # Top Mutual Fund Buying (mf_delta DESC)
        top_mf = sorted(analytics_records, key=lambda x: x["mf_delta"], reverse=True)[:10]
        
        # Pledge Reduction (pledged_delta ASC, only count where delta < 0)
        pledge_reductions = [x for x in analytics_records if x["pledged_delta"] < 0]
        top_pledge_red = sorted(pledge_reductions, key=lambda x: x["pledged_delta"])[:10]

        report_data = {
            "quarter": quarter,
            "previous_quarter": prev_quarter,
            "calculated_at": timestamp_now,
            "rankings": {
                "combined": [
                    {
                        "symbol": x["symbol"],
                        "name": x["name"],
                        "promoter_delta": x["promoter_delta"],
                        "fii_delta": x["fii_delta"],
                        "dii_delta": x["dii_delta"],
                        "mf_delta": x["mf_delta"],
                        "pledged_delta": x["pledged_delta"],
                        "why_ranked": f"Promoter +{x['promoter_delta']}%, FII +{x['fii_delta']}%, DII +{x['dii_delta']}%"
                    }
                    for x in top_combined
                ],
                "fii_buying": [{"symbol": x["symbol"], "name": x["name"], "delta": x["fii_delta"]} for x in top_fii],
                "promoter_buying": [{"symbol": x["symbol"], "name": x["name"], "delta": x["promoter_delta"]} for x in top_prom],
                "mf_buying": [{"symbol": x["symbol"], "name": x["name"], "delta": x["mf_delta"]} for x in top_mf],
                "pledge_reduction": [{"symbol": x["symbol"], "name": x["name"], "delta": x["pledged_delta"]} for x in top_pledge_red]
            }
        }

        # Save Report
        with transaction() as conn:
            # Deprecate old consolidated report
            conn.execute(
                "UPDATE analytics_reports SET is_current = 0 WHERE quarter = ? AND report_type = 'Consolidated Rankings'",
                (quarter,)
            )

            # Insert new report
            report_uuid = str(uuid.uuid4())
            conn.execute(
                """
                INSERT INTO analytics_reports (report_uuid, quarter, report_type, report_data, is_current, created_at, execution_id)
                VALUES (?, ?, 'Consolidated Rankings', ?, 1, ?, ?)
                """,
                (report_uuid, quarter, json.dumps(report_data), timestamp_now, execution_id)
            )

            # Log evidence of completion
            log_evidence(
                job_id=job_id,
                company_uuid="ALL",
                field_name="consolidated_analytics",
                value=f"Consolidated rankings generated for {quarter}. FII Buying leader: {top_fii[0]['symbol'] if top_fii else 'N/A'}",
                source="Analytics Engine",
                confidence_score=1.0,
                quarter=quarter,
                conn=conn
            )

        log_audit(
            job_id=job_id,
            step="Completion",
            action="Analytics calculation completed",
            metadata={"companies_analyzed": len(analytics_records), "report_uuid": report_uuid}
        )

        return {
            "status": "success",
            "quarter": quarter,
            "companies_analyzed": len(analytics_records),
            "top_fii_buying_leader": top_fii[0]["symbol"] if top_fii else None,
            "metrics": {
                "records_processed": len(analytics_records)
            }
        }
