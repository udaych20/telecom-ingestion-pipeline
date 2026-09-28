import os
import subprocess
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

from intent_app import run_stream_host
from stream_provision import AzureCLI, OWNER_TAG, PACKAGE_FILES, SOURCE_DEFAULTS, ensure_named, provision_stream


SUBSCRIPTION = '11111111-1111-1111-1111-111111111111'
ENV = {
    'AZURE_SUBSCRIPTION_ID': SUBSCRIPTION,
    'AZURE_RESOURCE_GROUP': 'stream-group',
    'AZURE_LOCATION': 'eastus',
    'AZURE_COSMOS_RESOURCE_GROUP': 'cosmos-group',
    'AZURE_COSMOS_ACCOUNT': 'existing-cosmos',
    'COSMOS_DATABASE': 'NORA',
    'COSMOS_ENDPOINT': 'https://existing-cosmos.documents.azure.com:443/',
    'EVENT_HUB_NAMESPACE': 'nora-events.servicebus.windows.net',
    'EVENT_HUB_NAME': 'updates',
    'EVENT_HUB_CONSUMER_GROUP': 'subscriber',
    'STREAM_STORAGE_ACCOUNT': 'norastreamstorage',
    'STREAM_FUNCTION_APP': 'nora-stream-functions',
}
APP_DIR = Path(__file__).resolve().parents[1] / 'event_app'


class FakeAzure:
    """Management-plane fake: a failed lookup is never represented as an empty list."""
    def __init__(self):
        self.subscription = SUBSCRIPTION
        self.calls = []
        self.group_exists = False
        self.resources = {}
        self.settings = {}
        self.principal = '22222222-2222-2222-2222-222222222222'
        self.cosmos_id = f'/subscriptions/{SUBSCRIPTION}/resourceGroups/cosmos-group/providers/Microsoft.DocumentDB/databaseAccounts/existing-cosmos'
        self.resources[('cosmosdb', 'sql', 'container')] = [
            {'name': name, 'resource': {'partitionKey': {'paths': ['/cid']}}}
            for name, _ in SOURCE_DEFAULTS.values()
        ]
        self.packages = []

    def run(self, *args):
        self.calls.append(args)
        command = args[:next((i for i, arg in enumerate(args) if arg.startswith('--')), len(args))]

        def option(name):
            return args[args.index(name) + 1]

        if command == ('account', 'show'):
            return {'id': SUBSCRIPTION}
        if command == ('cosmosdb', 'show'):
            return {'id': self.cosmos_id, 'documentEndpoint': 'https://existing-cosmos.documents.azure.com/'}
        if command == ('eventgrid', 'namespace', 'show'):
            return {'name': option('--name'), 'topicsConfiguration': {'hostname': 'existing-grid.westus2-1.eventgrid.azure.net'}}
        if command == ('role', 'definition', 'list'):
            return [{'name': '33333333-3333-3333-3333-333333333333' if option('--name').endswith('Sender')
                     else '44444444-4444-4444-4444-444444444444'}]
        if command == ('group', 'exists'):
            return self.group_exists
        if command == ('group', 'create'):
            self.group_exists = True
            return {}
        if command == ('functionapp', 'identity', 'assign'):
            return {'principalId': self.principal}
        if command == ('functionapp', 'config', 'appsettings', 'list'):
            return [{'name': name, 'value': value} for name, value in self.settings.items()]
        if command == ('functionapp', 'config', 'appsettings', 'set'):
            self.settings.update(item.split('=', 1) for item in args[args.index('--settings') + 1:])
            return []
        if command == ('functionapp', 'deployment', 'source', 'config-zip'):
            with zipfile.ZipFile(option('--src')) as archive:
                self.packages.append(archive.namelist())
            return {'status': 4}
        if command == ('tag', 'update'):
            name, value = option('--tags').split('=', 1)
            self.resources[('functionapp',)][0]['tags'][name] = value
            return {}
        if command[-1] == 'list':
            return self.resources.get(command[:-1], [])
        if command[-1] == 'create':
            key = command[:-1]
            if key == ('role', 'assignment'):
                resource = {'principalId': option('--assignee-object-id'),
                            'roleDefinitionId': f'/subscriptions/{SUBSCRIPTION}/providers/Microsoft.Authorization/roleDefinitions/' + option('--role')}
            elif key == ('cosmosdb', 'sql', 'role', 'assignment'):
                resource = {'principalId': option('--principal-id'), 'scope': self.cosmos_id + option('--scope'),
                            'roleDefinitionId': option('--role-definition-id')}
            else:
                resource = {'name': option('--name'), 'id': '/resources/' + option('--name')}
                if key == ('eventhubs', 'namespace'):
                    resource['sku'] = {'name': 'Standard'}
                if key == ('eventgrid', 'namespace', 'topic'):
                    resource['inputSchema'] = 'CloudEventSchemaV1_0'
                if key == ('eventgrid', 'namespace', 'topic', 'event-subscription'):
                    resource['deliveryConfiguration'] = {'deliveryMode': 'Queue'}
                if key == ('functionapp',):
                    resource['tags'] = {'managed-by': OWNER_TAG}
                if key == ('storage', 'account'):
                    resource['kind'] = 'StorageV2'
                if key == ('cosmosdb', 'sql', 'container'):
                    resource['resource'] = {'partitionKey': {'paths': ['/id']}}
            self.resources.setdefault(key, []).append(resource)
            return resource
        raise AssertionError(f'Unexpected Azure command: {command}')


@patch.dict(os.environ, ENV, clear=True)
class ProvisionTests(unittest.TestCase):
    def test_grid_creates_only_topic_subscription_and_reuses_namespace(self):
        fake = FakeAzure()
        grid_env = {'STREAM_TRANSPORT': 'eventgrid', 'EVENT_GRID_NAMESPACE': 'existing-grid',
                    'EVENT_GRID_RESOURCE_GROUP': 'grid-group', 'EVENT_GRID_TOPIC': 'updates',
                    'EVENT_GRID_SUBSCRIPTION': 'subscriber', 'EVENT_HUB_NAMESPACE': '',
                    'EVENT_HUB_NAME': '', 'EVENT_HUB_CONSUMER_GROUP': ''}
        with patch.dict(os.environ, grid_env), patch('stream_provision.AzureCLI', return_value=fake):
            provision_stream(APP_DIR)
            count = len(fake.calls)
            provision_stream(APP_DIR)
        self.assertFalse(any(call[0] == 'eventhubs' for call in fake.calls))
        self.assertFalse(any(call[:3] == ('eventgrid', 'namespace', 'create') for call in fake.calls))
        self.assertFalse(any('create' in call for call in fake.calls[count:]))
        self.assertEqual(fake.settings['STREAM_TRANSPORT'], 'eventgrid')
        self.assertEqual(fake.settings['EVENT_GRID_ENDPOINT'], 'https://existing-grid.westus2-1.eventgrid.azure.net')
        self.assertNotIn('EVENT_HUB_NAME', fake.settings)

    def test_grid_push_subscription_is_not_silently_reused(self):
        fake = FakeAzure()
        fake.resources[('eventgrid', 'namespace', 'topic', 'event-subscription')] = [
            {'name': 'subscriber', 'deliveryConfiguration': {'deliveryMode': 'Push'}}]
        grid_env = {'STREAM_TRANSPORT': 'eventgrid', 'EVENT_GRID_NAMESPACE': 'existing-grid',
                    'EVENT_GRID_RESOURCE_GROUP': 'grid-group', 'EVENT_GRID_TOPIC': 'updates',
                    'EVENT_GRID_SUBSCRIPTION': 'subscriber'}
        with patch.dict(os.environ, grid_env), patch('stream_provision.AzureCLI', return_value=fake):
            with self.assertRaisesRegex(ValueError, 'Queue'):
                provision_stream(APP_DIR)

    def test_grid_namespace_failure_has_no_creations_or_hub_fallback(self):
        fake = FakeAzure()
        original = fake.run

        def run(*args):
            if args[:3] == ('eventgrid', 'namespace', 'show'):
                raise RuntimeError('ResourceNotFound')
            return original(*args)

        fake.run = run
        env = {'STREAM_TRANSPORT': 'eventgrid', 'EVENT_GRID_NAMESPACE': 'missing-grid',
               'EVENT_GRID_RESOURCE_GROUP': 'grid-group', 'EVENT_GRID_TOPIC': 'updates',
               'EVENT_GRID_SUBSCRIPTION': 'subscriber'}
        with patch.dict(os.environ, env), patch('stream_provision.AzureCLI', return_value=fake):
            with self.assertRaisesRegex(RuntimeError, 'ResourceNotFound'):
                provision_stream(APP_DIR)
        self.assertFalse(any('create' in call or call[0] == 'eventhubs' for call in fake.calls))

    def test_create_then_reuse_and_package_allowlist(self):
        fake = FakeAzure()
        with patch('stream_provision.AzureCLI', return_value=fake):
            provision_stream(APP_DIR)
            first_count = len(fake.calls)
            provision_stream(APP_DIR)
        self.assertEqual(fake.packages, [list(PACKAGE_FILES)])
        self.assertFalse(any('create' in call for call in fake.calls[first_count:]))
        self.assertFalse(any('set' in call for call in fake.calls[first_count:]))
        creates = [call for call in fake.calls if call[:4] == ('cosmosdb', 'sql', 'container', 'create')]
        self.assertEqual(len(creates), 4)
        self.assertTrue(all(call[call.index('--name') + 1].startswith('leases-') for call in creates))
        self.assertEqual(fake.settings['COSMOS_CONNECTION__accountEndpoint'], ENV['COSMOS_ENDPOINT'])
        self.assertNotIn('AzureWebJobsStorage', fake.settings)
        self.assertNotIn('AZURE_CLIENT_SECRET', fake.settings)

    def test_manual_publish_mode(self):
        fake = FakeAzure()
        with patch.dict(os.environ, {'STREAM_DEPLOY_FUNCTION': 'false'}), patch('stream_provision.AzureCLI', return_value=fake):
            provision_stream(APP_DIR)
        self.assertFalse(fake.packages)
        self.assertTrue(fake.settings)

    def test_failed_publish_does_not_mark_package_deployed(self):
        fake = FakeAzure()
        original = fake.run

        def run(*args):
            if args[:4] == ('functionapp', 'deployment', 'source', 'config-zip'):
                raise RuntimeError('Remote build failed')
            return original(*args)

        fake.run = run
        with patch('stream_provision.AzureCLI', return_value=fake), self.assertRaisesRegex(RuntimeError, 'Remote build'):
            provision_stream(APP_DIR)
        self.assertNotIn('nora-package-sha256', fake.resources[('functionapp',)][0]['tags'])

    def test_missing_config_does_not_call_azure(self):
        with patch.dict(os.environ, {'AZURE_SUBSCRIPTION_ID': ''}), patch('stream_provision.AzureCLI') as cli:
            with self.assertRaisesRegex(ValueError, 'AZURE_SUBSCRIPTION_ID'):
                provision_stream(APP_DIR)
        cli.assert_not_called()

    def test_missing_source_is_not_created(self):
        fake = FakeAzure()
        fake.resources[('cosmosdb', 'sql', 'container')] = []
        with patch('stream_provision.AzureCLI', return_value=fake), self.assertRaisesRegex(ValueError, 'source containers'):
            provision_stream(APP_DIR)
        self.assertFalse(any('create' in call for call in fake.calls))

    def test_endpoint_mismatch_stops_before_writes(self):
        fake = FakeAzure()
        with patch.dict(os.environ, {'COSMOS_ENDPOINT': 'https://other.documents.azure.com/'}), patch('stream_provision.AzureCLI', return_value=fake):
            with self.assertRaisesRegex(ValueError, 'does not match'):
                provision_stream(APP_DIR)
        self.assertFalse(any('create' in call for call in fake.calls))

    def test_private_cosmos_stops_before_writes(self):
        fake = FakeAzure()
        original = fake.run

        def run(*args):
            result = original(*args)
            if args[:2] == ('cosmosdb', 'show'):
                result['publicNetworkAccess'] = 'Disabled'
            return result

        fake.run = run
        with patch('stream_provision.AzureCLI', return_value=fake), self.assertRaisesRegex(ValueError, 'network restrictions'):
            provision_stream(APP_DIR)
        self.assertFalse(any('create' in call for call in fake.calls))

    def test_unrelated_function_is_not_overwritten(self):
        fake = FakeAzure()
        fake.group_exists = True
        fake.resources[('functionapp',)] = [{'name': ENV['STREAM_FUNCTION_APP']}]
        with patch('stream_provision.AzureCLI', return_value=fake), self.assertRaisesRegex(ValueError, 'not owned'):
            provision_stream(APP_DIR)
        self.assertFalse(any('create' in call or 'set' in call for call in fake.calls))

    def test_bad_lease_partition_stops_before_writes(self):
        fake = FakeAzure()
        fake.resources[('cosmosdb', 'sql', 'container')].append(
            {'name': 'leases-chat', 'resource': {'partitionKey': {'paths': ['/wrong']}}})
        with patch('stream_provision.AzureCLI', return_value=fake), self.assertRaisesRegex(ValueError, '/id'):
            provision_stream(APP_DIR)
        self.assertFalse(any('create' in call for call in fake.calls))

    def test_live_auto_mode_does_not_start_local_host(self):
        with patch.dict(os.environ, {'STREAM_AUTO_PROVISION': 'true'}), patch('stream_provision.provision_stream') as provision, patch('intent_app.subprocess.run') as run:
            run_stream_host()
        provision.assert_called_once()
        run.assert_not_called()

    def test_existing_local_mode_is_preserved(self):
        with patch('intent_app.shutil.which', return_value='func'), patch('intent_app.subprocess.run') as run:
            run_stream_host()
        self.assertEqual(run.call_args.args[0][:2], ['func', 'start'])


class CLITests(unittest.TestCase):
    def test_lookup_failure_never_creates(self):
        cli = Mock()
        cli.run.side_effect = RuntimeError('AuthorizationFailed')
        with self.assertRaises(RuntimeError):
            ensure_named(cli, 'resource', ['list'], ['create'], 'test')
        cli.run.assert_called_once_with('list')

    @patch('stream_provision.shutil.which', return_value='az')
    @patch('stream_provision.subprocess.run')
    def test_errors_redact_cli_bodies_and_pin_subscription(self, run, which):
        run.return_value = subprocess.CompletedProcess([], 1, 'SECRET', 'ERROR: (AuthorizationFailed) SECRET')
        cli = AzureCLI(SUBSCRIPTION)
        with self.assertRaisesRegex(RuntimeError, 'AuthorizationFailed') as caught:
            cli.run('group', 'list')
        self.assertNotIn('SECRET', str(caught.exception))
        self.assertIn(SUBSCRIPTION, run.call_args.args[0])

    @patch('stream_provision.shutil.which', return_value='az')
    @patch('stream_provision.subprocess.run')
    def test_shell_metacharacters_rejected(self, run, which):
        cli = AzureCLI(SUBSCRIPTION)
        with self.assertRaises(ValueError):
            cli.run('group', 'create', '--name', 'name&command')
        run.assert_not_called()
