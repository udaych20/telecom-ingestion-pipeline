# Security and privacy

## Authentication

The application uses `DefaultAzureCredential`. Use Azure CLI credentials only for local development. Use managed identity or workload identity in deployed environments.

## Authorization

Grant the Cosmos DB Built-in Data Reader role at the narrowest account, database, or container scope that supports the workload. Do not grant write access to this reader unless a future requirement explicitly needs it.

## Secrets

- Never commit `.env`.
- Do not use Cosmos account keys in this application.
- Do not print credentials or tokens.
- Store any future destination credentials in an approved secret manager.

## Personal data

The examples include names, email addresses, device identifiers, telephone numbers, and interaction text. Treat all output as sensitive. Before LLM training:

1. Establish a lawful and approved purpose.
2. Remove or pseudonymize unnecessary identifiers.
3. Apply retention and deletion requirements.
4. Validate the destination model/provider's data controls.
5. Keep a traceable dataset version and approval record.

## Reporting

Report suspected credential exposure or unauthorized data output through the organization's security incident process. Do not include live customer records in public issues.
