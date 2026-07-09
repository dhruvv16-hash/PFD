import sys
from logger.logger import setup_global_logger, get_logger
from db.connection import init_db
from agents.universe_agent import UniverseAgent
from agents.company_agent import CompanyAgent
from agents.ownership_agents import (
    PromoterAgent, FiiAgent, DiiAgent, MutualFundAgent, PublicAgent, PledgeAgent
)
from agents.merge_agent import MergeAgent
from agents.analytics_agent import AnalyticsAgent
from agents.communication_agent import CommunicationAgent
from agents.trade_tracker import TradeTracker
from agents.hello_world import HelloWorldAgent
from orchestrator.orchestrator import WorkflowOrchestrator

def main():
    # 1. Setup logging
    setup_global_logger()
    logger = get_logger("runner")
    logger.info("Starting Ownership Intelligence Platform runner...")

    # 2. Initialize Database
    try:
        init_db()
        logger.info("Database schema initialized successfully.")
    except Exception as e:
        logger.critical(f"Failed to initialize database: {str(e)}")
        sys.exit(1)

    # 3. Instantiate Agent(s)
    universe_agent = UniverseAgent()
    company_agent = CompanyAgent()
    promoter_agent = PromoterAgent()
    fii_agent = FiiAgent()
    dii_agent = DiiAgent()
    mf_agent = MutualFundAgent()
    public_agent = PublicAgent()
    pledge_agent = PledgeAgent()
    merge_agent = MergeAgent()
    analytics_agent = AnalyticsAgent()
    communication_agent = CommunicationAgent()
    trade_tracker = TradeTracker()
    hello_agent = HelloWorldAgent()

    # 4. Instantiate and execute the pipeline orchestrator
    orchestrator = WorkflowOrchestrator(
        agents=[
            universe_agent,
            company_agent,
            promoter_agent,
            fii_agent,
            dii_agent,
            mf_agent,
            public_agent,
            pledge_agent,
            merge_agent,
            analytics_agent,
            communication_agent,
            trade_tracker,
            hello_agent
        ]
    )
    
    logger.info("Executing pipeline for Quarter 1 (2026-Q1)...")
    try:
        initial_inputs_q1 = {
            "text": "Ownership Intelligence Platform Foundation Active",
            "quarter": "2026-Q1"
        }
        results_q1 = orchestrator.run_pipeline(initial_inputs=initial_inputs_q1)
        logger.info(f"Q1 pipeline run completed successfully.")
    except Exception as e:
        logger.error(f"Q1 pipeline failed: {str(e)}")
        sys.exit(1)

    logger.info("Executing pipeline for Quarter 2 (2026-Q2)...")
    try:
        initial_inputs_q2 = {
            "text": "Ownership Intelligence Platform Foundation Active",
            "quarter": "2026-Q2"
        }
        results_q2 = orchestrator.run_pipeline(initial_inputs=initial_inputs_q2)
        logger.info(f"Q2 pipeline run completed successfully. Final output: {results_q2['HelloWorldAgent']['message']}")
    except Exception as e:
        logger.error(f"Q2 pipeline failed: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    main()
