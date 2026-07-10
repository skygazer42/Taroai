# Ecommerce Solution Pack Baseline

## Business Outcomes

Help ecommerce teams improve product listing quality, monitor competitor pricing, respond to buyer messages faster, and produce weekly operations summaries from governed tenant data.

## Taroai Resources

- Workspaces: ecommerce-operations, merchandising, customer-messaging.
- Agent templates: product-content-agent, competitor-monitor-agent, buyer-message-agent, weekly-ops-agent.
- Skills: ecommerce.product_description, ecommerce.price_monitor, ecommerce.buyer_message_assistant, ecommerce.weekly_operations_report.
- Knowledge spaces: catalog-guidelines, brand-voice, marketplace-policy, fulfillment-sop.
- Evaluation cases: listing-format, message-tone, price-monitor-source-check, weekly-report-artifact.

## Product Description

Creates approved product titles, bullet points, and listing descriptions from structured catalog data and brand guidelines.

## Competitor Price Monitor

Compares approved product SKUs against competitor snapshots or connector-provided market data and produces a pricing exception list.

## Buyer Message Assistant

Drafts buyer responses using order status, returns policy, and approved brand tone. Replies require human approval before external send.

## Operations Weekly Report

Summarizes order exceptions, fulfillment delays, return themes, and listing issues into an artifact for operations review.

## Required Connectors

- Ecommerce platform connector for product, order, and message data.
- Internal API connector for catalog and inventory records.
- Optional database connector for warehouse exceptions.

## Knowledge Spaces

- Product catalog guidelines.
- Brand voice and messaging policy.
- Returns and warranty policy.
- Fulfillment SOPs.

## Approval Gates

- External buyer replies require approval.
- Price changes require approval.
- Listings with regulated claims require approval.

## Sample Inputs

- SKU list for new product launch.
- Competitor snapshot CSV.
- Buyer message thread ID.
- Weekly operations date range.

## Artifacts

- Product description markdown.
- Price exception CSV.
- Buyer response draft.
- Weekly operations report.

## Success Metrics

- active_workspaces
- skills_installed
- runs_completed
- artifact_downloads
- approvals_resolved
