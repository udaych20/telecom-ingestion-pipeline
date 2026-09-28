"""Create the live pipeline's supporting resources using the signed-in Azure CLI."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import uuid
import zipfile
from pathlib import Path

from pipeline_logging import LOGGER, progress


SOURCE_DEFAULTS = {
    'COSMOS_CHAT_CONTAINER': ('chat-history-uat', 'leases-chat'),
    'COSMOS_TOOLS_CONTAINER': ('context-history-all-tools', 'leases-tools'),
    'COSMOS_CONTEXT_CONTAINER': ('context-history-uat', 'leases-context'),
    'COSMOS_FEEDBACK_CONTAINER': ('chat-feedback', 'leases-feedback'),
}
PACKAGE_FILES = ('function_app.py', 'host.json', 'requirements.txt')
OWNER_TAG = 'nora-intent-stream'


def required(name):
    value = os.getenv(name, '').strip()
    if not value or 'YOUR-' in value.upper():
        raise ValueError(f'{name} must be configured before automatic Azure setup')
    if any(character in value for character in '\r\n"%&|<>^'):
        raise ValueError(f'{name} contains unsupported characters')
    return value


def flag(name, default=False):
    value = os.getenv(name, str(default)).strip().lower()
    if value not in {'true', 'false'}:
        raise ValueError(f'{name} must be true or false')
    return value == 'true'


class AzureCLI:
    def __init__(self, subscription):
        self.executable = shutil.which('az')
        if not self.executable:
            raise RuntimeError('Install Azure CLI and run az login before automatic Azure setup')
        self.subscription = subscription

    def run(self, *args):
        if any(any(character in arg for character in '\r\n"%&|<>^') for arg in args):
            raise ValueError('Azure setup arguments contain unsupported shell characters')
        command = [self.executable, *args, '--subscription', self.subscription,
                   '--only-show-errors', '--output', 'json']
        # Never log command arguments or responses: Azure may return storage keys.
        option_start = next((i for i, arg in enumerate(args) if arg.startswith('--')), len(args))
        operation = ' '.join(args[:option_start])
        with progress(f'Azure setup: {operation}'):
            try:
                result = subprocess.run(command, capture_output=True, text=True, timeout=1800)
            except subprocess.TimeoutExpired:
                raise RuntimeError(f'Azure {operation} timed out; inspect the resource before rerunning') from None
            if result.returncode:
                # Preserve the service error code, but not potentially sensitive CLI output.
                code = re.search(r'\(([A-Za-z][A-Za-z0-9_.]+)\)', result.stderr)
                detail = code.group(1) if code else 'CLI command failed'
                raise RuntimeError(f'Azure {operation}: {detail}. Check az login, permissions, policy, '
                                   'resource names and deployment status in Azure. No resources were rolled back.')
        return json.loads(result.stdout) if result.stdout.strip() else None


def ensure_named(cli, label, list_args, create_args, name):
    """Only a successful inventory response can establish that a resource is absent."""
    resources = cli.run(*list_args)
    existing = next((item for item in resources if item['name'].split('/')[-1].lower() == name.lower()), None)
    if existing is not None:
        LOGGER.info('Reusing %s: %s', label, name)
        return existing
    LOGGER.info('Creating %s: %s', label, name)
    return cli.run(*create_args)


def grant_role(cli, principal, role, scope):
    assignments = cli.run('role', 'assignment', 'list', '--scope', scope)
    role_id = f'/subscriptions/{cli.subscription}/providers/Microsoft.Authorization/roleDefinitions/{role}'
    if any(item.get('principalId', '').lower() == principal.lower()
           and item.get('roleDefinitionId', '').lower() == role_id.lower() for item in assignments):
        return
    name = str(uuid.uuid5(uuid.NAMESPACE_URL, f'{scope.lower()}:{principal}:{role}'))
    cli.run('role', 'assignment', 'create', '--name', name, '--assignee-object-id', principal,
            '--assignee-principal-type', 'ServicePrincipal', '--role', role, '--scope', scope)


def grid_configuration(cli):
    """Inspect the user's existing namespace; never create a replacement namespace."""
    group = required('EVENT_GRID_RESOURCE_GROUP')
    namespace = required('EVENT_GRID_NAMESPACE')
    topic = required('EVENT_GRID_TOPIC')
    subscription = required('EVENT_GRID_SUBSCRIPTION')
    batch_size = int(os.getenv('EVENT_GRID_RECEIVE_BATCH_SIZE', '10'))
    if not 1 <= batch_size <= 100:
        raise ValueError('EVENT_GRID_RECEIVE_BATCH_SIZE must be between 1 and 100')
    resource = cli.run('eventgrid', 'namespace', 'show', '--resource-group', group, '--name', namespace)
    if resource.get('publicNetworkAccess') in {'Disabled', 'SecuredByPerimeter'} or resource.get('inboundIpRules'):
        raise ValueError('Event Grid namespace is network-restricted; provide an approved network-aware deployment')
    hostname = resource.get('topicsConfiguration', {}).get('hostname')
    if not hostname:
        raise ValueError('Event Grid namespace has no HTTP topics endpoint; MQTT topic spaces are not supported')
    endpoint = 'https://' + hostname
    configured = os.getenv('EVENT_GRID_ENDPOINT', '').strip().rstrip('/')
    if configured and configured != endpoint:
        raise ValueError('EVENT_GRID_ENDPOINT does not match the configured Event Grid namespace')
    roles = []
    for name in ('EventGrid Data Sender', 'EventGrid Data Receiver'):
        definitions = cli.run('role', 'definition', 'list', '--name', name)
        if len(definitions) != 1:
            raise ValueError(f'Cannot resolve Azure built-in role: {name}')
        roles.append(definitions[0]['name'])
    settings = {'STREAM_TRANSPORT': 'eventgrid', 'EVENT_GRID_ENDPOINT': endpoint,
                'EVENT_GRID_TOPIC': topic, 'EVENT_GRID_SUBSCRIPTION': subscription,
                'EVENT_GRID_RECEIVE_BATCH_SIZE': str(batch_size)}
    return group, namespace, settings, roles


def ensure_grid_topic(cli, config):
    group, namespace, settings, roles = config
    topic, subscription = settings['EVENT_GRID_TOPIC'], settings['EVENT_GRID_SUBSCRIPTION']
    args = ['--resource-group', group, '--namespace-name', namespace]
    resource = ensure_named(cli, 'Event Grid HTTP topic',
        ['eventgrid', 'namespace', 'topic', 'list', *args],
        ['eventgrid', 'namespace', 'topic', 'create', *args, '--name', topic,
         '--input-schema', 'CloudEventSchemaV1_0', '--publisher-type', 'Custom',
         '--event-retention-in-days', '1'], topic)
    if resource.get('inputSchema') != 'CloudEventSchemaV1_0':
        raise ValueError('Event Grid topic must accept CloudEvents 1.0')
    sub = ensure_named(cli, 'Event Grid pull subscription',
        ['eventgrid', 'namespace', 'topic', 'event-subscription', 'list', *args, '--topic-name', topic],
        ['eventgrid', 'namespace', 'topic', 'event-subscription', 'create', *args, '--topic-name', topic,
         '--name', subscription, '--event-delivery-schema', 'CloudEventSchemaV1_0',
         '--delivery-configuration',
         '{deliveryMode:Queue,queue:{receiveLockDurationInSeconds:300,maxDeliveryCount:10,eventTimeToLive:P1D}}'],
        subscription)
    if sub.get('deliveryConfiguration', {}).get('deliveryMode') != 'Queue':
        raise ValueError('Existing Event Grid subscription must use Queue (pull) delivery')
    return resource, settings, roles


def provision_stream(event_app_dir: Path):
    """Provision and optionally publish in Azure; never start a second local host."""
    subscription = required('AZURE_SUBSCRIPTION_ID')
    uuid.UUID(subscription)
    group = required('AZURE_RESOURCE_GROUP')
    location = required('AZURE_LOCATION')
    cosmos_group = required('AZURE_COSMOS_RESOURCE_GROUP')
    cosmos_account = required('AZURE_COSMOS_ACCOUNT')
    database = required('COSMOS_DATABASE')
    endpoint = required('COSMOS_ENDPOINT')
    transport = os.getenv('STREAM_TRANSPORT', 'eventhub').strip().lower()
    if transport not in {'eventhub', 'eventgrid'}:
        raise ValueError('STREAM_TRANSPORT must be eventhub or eventgrid')
    if transport == 'eventhub':
        namespace = required('EVENT_HUB_NAMESPACE').removesuffix('.servicebus.windows.net')
        hub = required('EVENT_HUB_NAME')
        consumer = required('EVENT_HUB_CONSUMER_GROUP')
    storage = required('STREAM_STORAGE_ACCOUNT')
    app_name = required('STREAM_FUNCTION_APP')
    deploy = flag('STREAM_DEPLOY_FUNCTION', True)
    if not re.fullmatch(r'[a-z0-9]{3,24}', storage):
        raise ValueError('STREAM_STORAGE_ACCOUNT must be 3-24 lowercase letters or digits')
    for name in ((namespace, app_name) if transport == 'eventhub' else (app_name,)):
        if not re.fullmatch(r'[a-zA-Z][a-zA-Z0-9-]{4,48}[a-zA-Z0-9]', name):
            raise ValueError('Use 6-50 letters, digits or hyphens for the namespace and Function App names')
    sources = {key: os.getenv(key, default).strip() for key, (default, _) in SOURCE_DEFAULTS.items()}
    leases = {key + '_LEASE': os.getenv(key + '_LEASE', lease).strip()
              for key, (_, lease) in SOURCE_DEFAULTS.items()}
    if any(not name or '/' in name for name in [database, *sources.values(), *leases.values()]):
        raise ValueError('Database, source and lease container names must be nonempty and contain no slash')
    if set(sources.values()) & set(leases.values()) or len(set(leases.values())) != len(leases):
        raise ValueError('Lease containers must be distinct from each other and from all source containers')
    for filename in PACKAGE_FILES:
        if not (event_app_dir / filename).is_file():
            raise ValueError(f'Missing Function deployment file: {filename}')

    cli = AzureCLI(subscription)
    cli.run('account', 'show')
    grid_config = grid_configuration(cli) if transport == 'eventgrid' else None
    cosmos_args = ['--resource-group', cosmos_group, '--account-name', cosmos_account]
    account = cli.run('cosmosdb', 'show', '--resource-group', cosmos_group, '--name', cosmos_account)
    if account['documentEndpoint'].rstrip('/').replace(':443', '') != endpoint.rstrip('/').replace(':443', ''):
        raise ValueError('AZURE_COSMOS_ACCOUNT does not match COSMOS_ENDPOINT; nothing was provisioned')
    if (account.get('publicNetworkAccess') == 'Disabled'
            or account.get('isVirtualNetworkFilterEnabled')
            or account.get('ipRules')):
        raise ValueError('Cosmos has network restrictions. This automatic setup cannot supply approved '
                         'VNet/private-endpoint routing. Ask your Azure administrator for a network-aware '
                         'Function deployment; nothing was provisioned and no firewall settings were changed.')
    db_args = [*cosmos_args, '--database-name', database]
    containers = cli.run('cosmosdb', 'sql', 'container', 'list', *db_args)
    existing_containers = {item['name']: item for item in containers}
    missing = set(sources.values()) - set(existing_containers)
    if missing:
        raise ValueError(f'Configured Cosmos source containers do not exist: {sorted(missing)}')
    for lease in leases.values():
        if lease in existing_containers:
            resource = existing_containers[lease]['resource']
            if resource['partitionKey']['paths'] != ['/id'] or resource.get('defaultTtl') not in (None, -1):
                raise ValueError(f'Lease container {lease} requires /id partitioning and no expiry')

    group_args = ['--resource-group', group]
    group_exists = cli.run('group', 'exists', '--name', group)
    if group_exists:
        apps = cli.run('functionapp', 'list', *group_args)
        existing_app = next((app for app in apps if app['name'].lower() == app_name.lower()), None)
        if existing_app and (existing_app.get('tags') or {}).get('managed-by') != OWNER_TAG:
            raise ValueError('The Function App is not owned by this setup. Choose a new dedicated STREAM_FUNCTION_APP name')
    else:
        cli.run('group', 'create', '--name', group, '--location', location)

    if transport == 'eventgrid':
        transport_resource, transport_settings, transport_roles = ensure_grid_topic(cli, grid_config)
    else:
        namespace_resource = ensure_named(cli, 'Event Hubs namespace',
            ['eventhubs', 'namespace', 'list', *group_args],
            ['eventhubs', 'namespace', 'create', *group_args, '--name', namespace,
             '--location', location, '--sku', 'Standard', '--capacity', '1'], namespace)
        if namespace_resource.get('sku', {}).get('name') not in {'Standard', 'Premium', 'Dedicated'}:
            raise ValueError('The Event Hubs namespace must support a dedicated consumer group (Standard or higher)')
        namespace_args = [*group_args, '--namespace-name', namespace]
        transport_resource = ensure_named(cli, 'Event Hub',
            ['eventhubs', 'eventhub', 'list', *namespace_args],
            ['eventhubs', 'eventhub', 'create', *namespace_args, '--name', hub, '--partition-count', '2'], hub)
        ensure_named(cli, 'consumer group',
            ['eventhubs', 'eventhub', 'consumer-group', 'list', *namespace_args, '--eventhub-name', hub],
            ['eventhubs', 'eventhub', 'consumer-group', 'create', *namespace_args,
             '--eventhub-name', hub, '--name', consumer], consumer)
        transport_roles = ['2b629674-e913-4c01-ae53-ef4638d8f975', 'a638d3c7-ab3a-418d-83e6-5f17a39d4fde']
        transport_settings = {'STREAM_TRANSPORT': 'eventhub',
            'EVENT_HUB_NAMESPACE': namespace + '.servicebus.windows.net',
            'EVENT_HUB_CONNECTION__fullyQualifiedNamespace': namespace + '.servicebus.windows.net',
            'EVENT_HUB_CONNECTION__credential': 'managedidentity',
            'EVENT_HUB_NAME': hub, 'EVENT_HUB_CONSUMER_GROUP': consumer}
    storage_resource = ensure_named(cli, 'Function storage account',
        ['storage', 'account', 'list', *group_args],
        ['storage', 'account', 'create', *group_args, '--name', storage, '--location', location,
         '--sku', 'Standard_LRS', '--kind', 'StorageV2', '--min-tls-version', 'TLS1_2',
         '--allow-blob-public-access', 'false'], storage)
    if storage_resource.get('kind') not in {'Storage', 'StorageV2'}:
        raise ValueError('Function hosting requires a general-purpose Storage or StorageV2 account')
    if storage_resource.get('allowSharedKeyAccess') is False:
        raise ValueError('Function storage disables shared keys. This setup uses CLI-managed host storage; '
                         'ask your administrator for an identity-based hosting deployment. No policy was changed.')
    if (storage_resource.get('publicNetworkAccess') == 'Disabled'
            or storage_resource.get('networkRuleSet', {}).get('defaultAction') == 'Deny'):
        raise ValueError('Function storage is network-restricted; use an administrator-approved '
                         'network-aware Function deployment. No storage firewall settings were changed.')
    for lease in leases.values():
        if lease not in existing_containers:
            LOGGER.info('Creating Cosmos lease container: %s', lease)
            cli.run('cosmosdb', 'sql', 'container', 'create', *db_args, '--name', lease,
                    '--partition-key-path', '/id')

    app = ensure_named(cli, 'Function App', ['functionapp', 'list', *group_args],
        ['functionapp', 'create', *group_args, '--name', app_name, '--storage-account', storage,
         '--flexconsumption-location', location, '--runtime', 'python', '--runtime-version', '3.11',
         '--functions-version', '4', '--https-only', 'true', '--assign-identity', '[system]',
         '--disable-app-insights', 'true', '--tags', f'managed-by={OWNER_TAG}'], app_name)
    app_args = [*group_args, '--name', app_name]
    identity = cli.run('functionapp', 'identity', 'assign', *app_args)
    principal = identity['principalId']
    for role in transport_roles:
        grant_role(cli, principal, role, transport_resource['id'])
    assignments = cli.run('cosmosdb', 'sql', 'role', 'assignment', 'list', *cosmos_args)
    permissions = [(name, '00000000-0000-0000-0000-000000000001') for name in sorted(set(sources.values()))]
    permissions += [(name, '00000000-0000-0000-0000-000000000002') for name in leases.values()]
    for container, role in permissions:
        relative_scope = f'/dbs/{database}/colls/{container}'
        scope = account['id'] + relative_scope
        role_id = f"{account['id']}/sqlRoleDefinitions/{role}"
        if any(item['principalId'] == principal and item['scope'].lower() == scope.lower()
               and item['roleDefinitionId'].lower() == role_id.lower() for item in assignments):
            continue
        cli.run('cosmosdb', 'sql', 'role', 'assignment', 'create', *cosmos_args,
                '--role-assignment-id', str(uuid.uuid5(uuid.NAMESPACE_URL, scope + principal + role)),
                '--principal-id', principal, '--role-definition-id', role_id, '--scope', relative_scope)

    settings = {'COSMOS_CONNECTION__accountEndpoint': endpoint, 'COSMOS_DATABASE': database,
                'COSMOS_CONNECTION__credential': 'managedidentity',
                **transport_settings,
                'NORA_LOG_LEVEL': os.getenv('NORA_LOG_LEVEL', 'INFO'), **sources, **leases}
    current = {item['name']: item['value'] for item in cli.run('functionapp', 'config', 'appsettings', 'list', *app_args)}
    changes = [f'{key}={value}' for key, value in settings.items() if current.get(key) != value]
    if changes:
        cli.run('functionapp', 'config', 'appsettings', 'set', *app_args, '--settings', *changes)
    if not deploy:
        LOGGER.info('Azure resources configured. Manual code deployment is required for %s; '
                    'set STREAM_DEPLOY_FUNCTION=true to publish automatically.', app_name)
        return
    digest = hashlib.sha256()
    for filename in PACKAGE_FILES:
        digest.update(filename.encode())
        digest.update((event_app_dir / filename).read_bytes())
    package_hash = digest.hexdigest()
    if (app.get('tags') or {}).get('nora-package-sha256') == package_hash:
        LOGGER.info('Function code is unchanged; skipping deployment to %s', app_name)
    else:
        with tempfile.TemporaryDirectory(prefix='nora-deploy-') as directory:
            package = Path(directory) / 'event_app.zip'
            with zipfile.ZipFile(package, 'w', zipfile.ZIP_DEFLATED) as archive:
                for filename in PACKAGE_FILES:
                    archive.write(event_app_dir / filename, filename)
            cli.run('functionapp', 'deployment', 'source', 'config-zip', *app_args,
                    '--src', str(package), '--build-remote', 'true')
        cli.run('tag', 'update', '--resource-id', app['id'], '--operation', 'Merge',
                '--tags', f'nora-package-sha256={package_hash}')
    functions = cli.run('functionapp', 'function', 'list', *app_args)
    expected = {'chat_history_changes', 'tool_history_changes', 'context_history_changes',
                'feedback_changes', 'nora_grid_subscriber' if transport == 'eventgrid' else 'nora_update_subscriber'}
    registered = {item['name'].split('/')[-1] for item in functions}
    if expected - registered:
        LOGGER.warning('Deployment submitted, but these functions are not registered yet: %s. '
                       'Check Function startup/build diagnostics in Azure.', ', '.join(sorted(expected - registered)))
    LOGGER.info('Azure Function setup finished: %s. No local Functions host will start. '
                'Verify trigger startup and event delivery in Azure; role propagation can take several minutes.', app_name)
