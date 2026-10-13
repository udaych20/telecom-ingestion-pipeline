# Kafka live mode

Kafka is a third live transport alongside Event Hubs and Event Grid. The four
Cosmos change-feed triggers publish compact CloudEvent JSON messages to an
existing topic. The source Cosmos document is never placed on Kafka.

```dotenv
STREAM_TRANSPORT=kafka
KAFKA_BOOTSTRAP_SERVERS=kafka-1.example.net:9093,kafka-2.example.net:9093
KAFKA_TOPIC=nora-updates
KAFKA_CLIENT_ID=nora-intent-finder
KAFKA_SECURITY_PROTOCOL=SASL_SSL
KAFKA_SASL_MECHANISM=PLAIN
KAFKA_SASL_USERNAME=YOUR-USERNAME
KAFKA_SASL_PASSWORD=YOUR-KEY-VAULT-REFERENCE
KAFKA_SSL_CAFILE=
KAFKA_RETRIES=5
KAFKA_REQUEST_TIMEOUT_MS=30000
KAFKA_PUBLISH_TIMEOUT_SECONDS=30
```

Supported security protocols are `PLAINTEXT`, `SSL`, `SASL_PLAINTEXT`, and
`SASL_SSL`. Production should use `SSL` or `SASL_SSL`. `PLAIN`, `SCRAM-SHA-256`,
and `SCRAM-SHA-512` can be supplied through `KAFKA_SASL_MECHANISM`. Store Kafka
credentials in Key Vault-backed Function App settings, not in a committed file.

The Azure Function must have DNS and network routing to every advertised Kafka
broker, not only the bootstrap address. For an on-prem cluster this normally
requires VNet integration plus VPN or ExpressRoute, firewall approval, and a
trusted CA chain. `KAFKA_SSL_CAFILE` is optional when the broker certificate is
already trusted by the Function host.
Automatic ZIP deployment does not copy an arbitrary CA file. When a private CA
is required, include the bundle in an approved custom deployment package and set
`KAFKA_SSL_CAFILE` to that deployed path.

Automatic setup validates and deploys the Function App but does not create,
configure, or grant access to the Kafka cluster or topic. Kafka mode registers
only the Cosmos change-feed Functions; consumption and intent processing occur
on the on-prem side.

Messages use the pseudonymized CID/run identifier as the Kafka key, preserving
ordering for the same interaction. Producer acknowledgement is `all`; a send
failure is raised so the Cosmos trigger invocation can retry from its checkpoint.
