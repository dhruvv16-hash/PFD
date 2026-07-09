import json
from datetime import datetime, timezone
from db.connection import transaction
from logger.logger import get_logger

logger = get_logger("audit")

def log_audit(job_id: str, step: str, action: str, metadata: dict = None, conn=None):
    """Logs a detailed step/action for an active job execution."""
    timestamp = datetime.now(timezone.utc).isoformat()
    metadata_json = json.dumps(metadata) if metadata else None
    
    if conn is not None:
        conn.execute(
            """
            INSERT INTO audit_logs (job_id, step, action, timestamp, metadata)
            VALUES (?, ?, ?, ?, ?)
            """,
            (job_id, step, action, timestamp, metadata_json)
        )
        logger.debug(f"[Audit] Job {job_id} | Step: {step} | Action: {action}")
    else:
        try:
            with transaction() as new_conn:
                new_conn.execute(
                    """
                    INSERT INTO audit_logs (job_id, step, action, timestamp, metadata)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (job_id, step, action, timestamp, metadata_json)
                )
            logger.debug(f"[Audit] Job {job_id} | Step: {step} | Action: {action}")
        except Exception as e:
            logger.error(f"Failed to log audit event: {str(e)}")

def log_evidence(
    job_id: str,
    company_uuid: str,
    field_name: str,
    value: str,
    source: str,
    source_doc_link: str = None,
    file_hash: str = None,
    confidence_score: float = 1.0,
    quarter: str = None,
    conn=None
):
    """Records an evidence entry representing data provenance."""
    timestamp = datetime.now(timezone.utc).isoformat()
    
    if conn is not None:
        conn.execute(
            """
            INSERT INTO evidence_records (
                job_id, company_uuid, field_name, value, source, 
                source_doc_link, file_hash, confidence_score, quarter, timestamp
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id, company_uuid, field_name, value, source,
                source_doc_link, file_hash, confidence_score, quarter, timestamp
            )
        )
        logger.debug(f"[Evidence] Job {job_id} | Field: {field_name} = {value} (Source: {source})")
    else:
        try:
            with transaction() as new_conn:
                new_conn.execute(
                    """
                    INSERT INTO evidence_records (
                        job_id, company_uuid, field_name, value, source, 
                        source_doc_link, file_hash, confidence_score, quarter, timestamp
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        job_id, company_uuid, field_name, value, source,
                        source_doc_link, file_hash, confidence_score, quarter, timestamp
                    )
                )
            logger.debug(f"[Evidence] Job {job_id} | Field: {field_name} = {value} (Source: {source})")
        except Exception as e:
            logger.error(f"Failed to log evidence: {str(e)}")
