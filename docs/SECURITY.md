# Security and privacy

## Authentication

The application uses `DefaultAzureCredential`. Use Azure CLI credentials only for local development. Use managed identity or workload identity in deployed environments.

## Authorization

Grant the Cosmos DB Built-in Data Reader role at the narrowest account, database, or container scope that supports the workload. Do not grant write access to this reader unless a future requirement explicitly needs it.

## Secrets

- Never commit `.env`.
- Do not use Cosmos account keys in this application.
- Do not print credentials or tokens.
- Store `PII_TOKENIZATION_KEY` and destination credentials in an approved secret manager such as Key Vault.

## Personal data

The examples include names, email addresses, device identifiers, telephone numbers, and interaction text. Records are recursively masked immediately after the Cosmos joins complete and before they reach classification or export code. CID, run, conversation, session, thread, interaction, and live-event document identifiers are replaced with stable tokens. Set the same strong `PII_TOKENIZATION_KEY` in initial-load and live deployments so tokens correlate without exposing plaintext. Add organization-specific telecom fields through `PII_ADDITIONAL_SENSITIVE_FIELDS`. Cosmos metadata remains available for audit, so output must still be handled as sensitive. Before LLM training:

1. Establish a lawful and approved purpose.
2. Remove or pseudonymize unnecessary identifiers.
3. Apply retention and deletion requirements.
4. Validate the destination model/provider's data controls.
5. Keep a traceable dataset version and approval record.

Dataset mode can export every accessible chat record. Use the narrowest Cosmos role scope, a controlled output directory, and a positive `BATCH_LIMIT` during validation to reduce accidental exposure.

## Reporting

Report suspected credential exposure or unauthorized data output through the organization's security incident process. Do not include live customer records in public issues.
