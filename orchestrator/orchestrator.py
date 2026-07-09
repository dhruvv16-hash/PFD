import json
import time
from datetime import datetime, timezone
import uuid
from typing import List, Dict, Any

from config.settings import settings
from logger.logger import get_logger, setup_pipeline_logger, remove_pipeline_logger
from db.connection import transaction, get_connection
from agents.base import BaseAgent

logger = get_logger("orchestrator")

class PipelineConcurrencyError(Exception):
    """Raised when trying to run an already active or completed pipeline."""
    pass

class DatabaseConnectionError(Exception):
    """Raised when the database is unavailable."""
    pass

class WorkflowOrchestrator:
    def __init__(self, agents: List[BaseAgent]):
        self.agents = agents

    def _check_db_health(self):
        """Verifies if the database is available and initialized."""
        try:
            conn = get_connection()
            conn.execute("SELECT 1;")
            conn.close()
        except Exception as e:
            raise DatabaseConnectionError(f"Database connection failed: {str(e)}")

    def run_pipeline(self, execution_id: str = None, initial_inputs: Dict[str, Any] = None, resume: bool = False) -> Dict[str, Any]:
        """
        Runs the registered agents in sequence, handling checkpoint resume,
        duplicate prevention, and exponential backoff retry.
        """
        self._check_db_health()

        if not execution_id:
            execution_id = str(uuid.uuid4())
            
        initial_inputs = initial_inputs or {}
        
        # Setup file logging for this pipeline run
        pipeline_log_handler = setup_pipeline_logger(execution_id)
        
        logger.info(f"Starting pipeline execution run {execution_id} (resume={resume})")
        
        # Verify run state in database to prevent concurrent/duplicate execution
        with transaction() as conn:
            row = conn.execute(
                "SELECT status FROM pipeline_executions WHERE execution_id = ?",
                (execution_id,)
            ).fetchone()
            
            if row:
                status = row["status"]
                if status == "Running":
                    raise PipelineConcurrencyError(f"Execution {execution_id} is already Running.")
                elif status == "Completed" and not resume:
                    raise PipelineConcurrencyError(f"Execution {execution_id} has already completed successfully.")
                elif status == "Failed" and not resume:
                    raise PipelineConcurrencyError(f"Execution {execution_id} failed in a previous run. To restart it, set resume=True.")
                
                # If we are resuming, set status back to Running
                conn.execute(
                    "UPDATE pipeline_executions SET status = 'Running', finished_at = NULL, error_message = NULL WHERE execution_id = ?",
                    (execution_id,)
                )
            else:
                if resume:
                    raise ValueError(f"Cannot resume execution {execution_id} as it does not exist.")
                
                # New pipeline execution
                started_at_str = datetime.now(timezone.utc).isoformat()
                conn.execute(
                    "INSERT INTO pipeline_executions (execution_id, status, started_at) VALUES (?, ?, ?)",
                    (execution_id, 'Running', started_at_str)
                )

        pipeline_state = {"initial_inputs": initial_inputs}
        completed_agents = set()
        
        # If resuming, load previously completed jobs
        if resume:
            with transaction() as conn:
                rows = conn.execute(
                    "SELECT agent_name, results FROM job_executions WHERE execution_id = ? AND status = 'Completed'",
                    (execution_id,)
                ).fetchall()
                for row in rows:
                    agent_name = row["agent_name"]
                    completed_agents.add(agent_name)
                    pipeline_state[agent_name] = json.loads(row["results"])
            logger.info(f"Loaded {len(completed_agents)} completed agent results from checkpoint.")

        try:
            for agent in self.agents:
                if agent.name in completed_agents:
                    logger.info(f"Skipping completed agent: {agent.name} (loaded from checkpoint)")
                    continue
                
                # Invoke the agent with exponential backoff retry logic
                attempt = 1
                success = False
                last_error = None
                
                while attempt <= settings.max_retries:
                    try:
                        # Construct inputs for the agent (includes the full running state)
                        agent_inputs = {
                            "pipeline_state": pipeline_state,
                            "execution_id": execution_id
                        }
                        
                        outputs = agent.run(execution_id=execution_id, inputs=agent_inputs, run_number=attempt)
                        pipeline_state[agent.name] = outputs
                        success = True
                        break
                    except Exception as e:
                        last_error = e
                        logger.warning(f"Agent {agent.name} failed on attempt {attempt}.")
                        
                        if attempt < settings.max_retries:
                            sleep_time = settings.retry_base_delay * (settings.retry_multiplier ** (attempt - 1))
                            logger.info(f"Backing off for {sleep_time} seconds before retrying {agent.name}...")
                            time.sleep(sleep_time)
                        attempt += 1
                
                if not success:
                    raise last_error

            # If all agents complete, mark pipeline execution as Completed
            finished_at_str = datetime.now(timezone.utc).isoformat()
            with transaction() as conn:
                conn.execute(
                    "UPDATE pipeline_executions SET status = 'Completed', finished_at = ? WHERE execution_id = ?",
                    (finished_at_str, execution_id)
                )
            logger.info(f"Pipeline execution {execution_id} completed successfully.")
            return pipeline_state

        except Exception as e:
            logger.error(f"Pipeline execution {execution_id} failed: {str(e)}")
            finished_at_str = datetime.now(timezone.utc).isoformat()
            with transaction() as conn:
                conn.execute(
                    "UPDATE pipeline_executions SET status = 'Failed', finished_at = ?, error_message = ? WHERE execution_id = ?",
                    (finished_at_str, str(e), execution_id)
                )
            raise e
        finally:
            remove_pipeline_logger(pipeline_log_handler)
