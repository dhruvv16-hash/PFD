import abc
import hashlib
import json
import time
import uuid
from datetime import datetime, timezone
from logger.logger import get_logger
from db.connection import transaction

logger = get_logger("agent")

class AgentValidationError(Exception):
    """Raised when agent inputs or outputs fail validation."""
    pass

class BaseAgent(abc.ABC):
    def __init__(self, name: str, version: str, role: str):
        self.name = name
        self.version = version
        self.role = role

    def _hash_data(self, data: dict) -> str:
        """Utility to calculate SHA-256 hash of a dictionary."""
        try:
            serialized = json.dumps(data, sort_keys=True, default=str)
            return hashlib.sha256(serialized.encode('utf-8')).hexdigest()
        except Exception:
            return "unknown-hash"

    def run(self, execution_id: str, inputs: dict, run_number: int = 1) -> dict:
        """
        Executes the agent logic with standard lifecycle tracking:
        input hashing, validation, timing, error capture, and output hashing.
        """
        job_id = str(uuid.uuid4())
        started_at_dt = datetime.now(timezone.utc)
        started_at_str = started_at_dt.isoformat()
        input_hash = self._hash_data(inputs)
        
        logger.info(f"[{self.name}] Starting job {job_id} (Attempt {run_number}) for execution {execution_id}")
        
        # Write initial Pending/Running state to database
        with transaction() as conn:
            conn.execute(
                """
                INSERT INTO job_executions (job_id, execution_id, agent_name, status, run_number, started_at, input_hash)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (job_id, execution_id, self.name, 'Running', run_number, started_at_str, input_hash)
            )

        try:
            # 1. Validate Inputs
            self.validate_inputs(inputs)
            
            # 2. Execute business logic
            outputs = self.execute(inputs, job_id=job_id)
            
            # 3. Validate Outputs
            self.validate_outputs(outputs)
            
            finished_at_dt = datetime.now(timezone.utc)
            finished_at_str = finished_at_dt.isoformat()
            duration = (finished_at_dt - started_at_dt).total_seconds()
            output_hash = self._hash_data(outputs)
            
            # Extract standard metrics
            records_processed = outputs.get("metrics", {}).get("records_processed", 0)
            output_size = len(json.dumps(outputs, default=str))
            metrics = {
                "records_processed": records_processed,
                "output_size_bytes": output_size,
                "custom_metrics": outputs.get("metrics", {})
            }
            
            # Update database status to Completed
            with transaction() as conn:
                conn.execute(
                    """
                    UPDATE job_executions
                    SET status = 'Completed', finished_at = ?, duration_seconds = ?, output_hash = ?, results = ?, metrics = ?
                    WHERE job_id = ?
                    """,
                    (finished_at_str, duration, output_hash, json.dumps(outputs, default=str), json.dumps(metrics), job_id)
                )
                
            logger.info(f"[{self.name}] Completed job {job_id} in {duration:.2f} seconds")
            return outputs

        except Exception as e:
            finished_at_dt = datetime.now(timezone.utc)
            finished_at_str = finished_at_dt.isoformat()
            duration = (finished_at_dt - started_at_dt).total_seconds()
            error_msg = str(e)
            
            logger.error(f"[{self.name}] Failed job {job_id} on attempt {run_number}: {error_msg}")
            
            # Update database status to Failed
            with transaction() as conn:
                conn.execute(
                    """
                    UPDATE job_executions
                    SET status = 'Failed', finished_at = ?, duration_seconds = ?, error_message = ?
                    WHERE job_id = ?
                    """,
                    (finished_at_str, duration, error_msg, job_id)
                )
            
            # Re-raise to allow orchestrator to handle retry/halt logic
            raise e

    @abc.abstractmethod
    def execute(self, inputs: dict, job_id: str) -> dict:
        """Core execution logic containing the agent's work. Must return a dict."""
        pass

    def validate_inputs(self, inputs: dict):
        """Validates input payload. Override in subclasses as needed."""
        if not isinstance(inputs, dict):
            raise AgentValidationError("Inputs must be a dictionary")

    def validate_outputs(self, outputs: dict):
        """Validates output payload. Override in subclasses as needed."""
        if not isinstance(outputs, dict):
            raise AgentValidationError("Outputs must be a dictionary")
