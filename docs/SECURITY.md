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
- Grant the change-feed Function read access to the monitored containers, write
  access only to its lease containers, and sender access only to the selected
  broker.
- Grant the subscriber receiver access to Event Hubs and data access to its
  dedicated Blob checkpoint container. Do not grant broker-management roles.
- Inject Kafka SASL credentials from Key Vault or the hosting platform's secret
  store; never place production credentials in `.env` or local settings files.

## Personal data

The examples include names, email addresses, device identifiers, telephone numbers, and interaction text. Treat all output as sensitive. Before LLM training:

1. Establish a lawful and approved purpose.
2. Remove or pseudonymize unnecessary identifiers.
3. Apply retention and deletion requirements.
4. Validate the destination model/provider's data controls.
5. Keep a traceable dataset version and approval record.

Dataset mode can export every accessible chat record. Use the narrowest Cosmos role scope, a controlled output directory, and a positive `BATCH_LIMIT` during validation to reduce accidental exposure.

Live notifications deliberately exclude source documents and message content.
They still contain record and correlation identifiers, so broker access, network
paths, retention, and diagnostic logs must follow the same data-classification
policy as the source system.

## Reporting

Report suspected credential exposure or unauthorized data output through the organization's security incident process. Do not include live customer records in public issues.
