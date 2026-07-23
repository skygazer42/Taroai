---
name: Document Evidence Brief
description: Turn supplied text or an attached text, Markdown, JSON, ZIP, or DOCX document into a grounded brief.
license: Apache-2.0
---

# Document Evidence Brief

Summarize only the content supplied by the user or present in attached files.

## Procedure

1. If neither text nor a readable attachment is available, ask for one. Never invent document contents.
2. For attachments, inspect file type and size before reading. Use Python's standard library for text, JSON, ZIP, and DOCX containers; do not install packages or use the network.
3. Identify the document's purpose, main claims, decisions, dates, owners, risks, and unresolved questions.
4. Tie every material conclusion to a section, heading, page label, or short excerpt when available.
5. Return a concise summary, key evidence, action items, and caveats in the user's language.

Do not reproduce secrets or unnecessary personal data. State unsupported or unreadable formats plainly.
