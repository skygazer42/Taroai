from pathlib import Path


SOLUTION_PACK_DOCS = {
    "ecommerce": [
        "Product Description",
        "Competitor Price Monitor",
        "Buyer Message Assistant",
        "Operations Weekly Report",
    ],
    "sales": [
        "Account Research",
        "Proposal Generator",
        "CRM Update Assistant",
        "Meeting Brief",
    ],
    "support": [
        "Ticket Triage",
        "Knowledge Answer Draft",
        "QA Review",
        "Escalation Summary",
    ],
    "operations": [
        "SOP Executor",
        "Spreadsheet Cleanup",
        "Vendor Research",
        "Report Builder",
    ],
}

SOLUTION_PACK_REQUIRED_SECTIONS = [
    "Required Connectors",
    "Knowledge Spaces",
    "Approval Gates",
    "Sample Inputs",
    "Artifacts",
    "Success Metrics",
]

CUSTOMER_SUCCESS_DOCS = {
    "rollout-playbook.md": [
        "Discovery",
        "Sandbox Tenant",
        "Data And Connector Setup",
        "Pilot",
        "Training",
        "Production",
        "Expansion",
        "Go-Live Readiness",
        "2026-07-01-13-enterprise-onboarding.md",
    ],
    "admin-training.md": [
        "Tenant Setup",
        "Roles",
        "Knowledge",
        "Skills",
        "Approvals",
        "Audit",
        "Billing",
    ],
    "employee-training.md": [
        "Chat And Task Console",
        "Artifacts",
        "Approvals",
        "Sharing",
        "Feedback",
        "Safe Use",
    ],
    "solution-engineer-checklist.md": [
        "Discovery",
        "Custom Skill Delivery",
        "Knowledge And Connector Readiness",
        "Evaluation And Approval",
        "Go-Live",
    ],
}


def test_industry_solution_pack_docs_define_business_resources_and_metrics():
    base_path = Path("docs/solution-packs")

    for pack_name, use_cases in SOLUTION_PACK_DOCS.items():
        text = (base_path / f"{pack_name}.md").read_text(encoding="utf-8")
        for use_case in use_cases:
            assert use_case in text
        for section in SOLUTION_PACK_REQUIRED_SECTIONS:
            assert f"## {section}" in text
        assert "Business Outcomes" in text
        assert "Taroai Resources" in text


def test_customer_success_docs_cover_rollout_and_training_contracts():
    base_path = Path("docs/customer-success")

    for filename, required_terms in CUSTOMER_SUCCESS_DOCS.items():
        text = (base_path / filename).read_text(encoding="utf-8")
        for term in required_terms:
            assert term in text
