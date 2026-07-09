import uuid
from agents.base import BaseAgent, AgentValidationError
from audit.audit import log_audit, log_evidence

class HelloWorldAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            name="HelloWorldAgent",
            version="1.0.0",
            role="Validates core infrastructure pipeline execution"
        )

    def validate_inputs(self, inputs: dict):
        super().validate_inputs(inputs)
        pipeline_state = inputs.get("pipeline_state", {})
        initial_inputs = pipeline_state.get("initial_inputs", {})
        if "text" not in initial_inputs:
            raise AgentValidationError("Missing required input field: 'text' in pipeline_state['initial_inputs']")

    def execute(self, inputs: dict, job_id: str) -> dict:
        text_input = inputs["pipeline_state"]["initial_inputs"]["text"]
        
        # Log audit step 1: Initialization
        log_audit(
            job_id=job_id,
            step="Initialization",
            action="Read input payload",
            metadata={"input_length": len(text_input)}
        )
        
        # Log audit step 2: Computation
        processed_text = text_input.upper()
        log_audit(
            job_id=job_id,
            step="Computation",
            action="Convert text to uppercase",
            metadata={"output_length": len(processed_text)}
        )

        # Log mock evidence for visual inspection of the audit layer
        mock_company_uuid = str(uuid.uuid5(uuid.NAMESPACE_DNS, "nseindia.com"))
        log_evidence(
            job_id=job_id,
            company_uuid=mock_company_uuid,
            field_name="mock_field_count",
            value="42",
            source="NSE Official PDF",
            source_doc_link="https://www.nseindia.com/filings/mock.pdf",
            file_hash="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            confidence_score=0.98,
            quarter="2026-Q1"
        )

        # Log audit step 3: Termination
        log_audit(
            job_id=job_id,
            step="Termination",
            action="Assemble final response package"
        )

        return {
            "status": "success",
            "message": f"Hello World! Processed text: {processed_text}",
            "metrics": {
                "records_processed": 1
            }
        }
