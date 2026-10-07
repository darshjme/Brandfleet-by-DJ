import importlib.util
import json
import os
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen


class ControllerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        os.environ['BRANDFLEET_STATE'] = cls.temp.name
        os.environ['BRANDFLEET_CONTROLLER_TOKEN'] = 'local-test-only-token'
        spec = importlib.util.spec_from_file_location('controller', os.path.join(os.path.dirname(__file__), 'controller.py'))
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)
        cls.module.ROUTE_LOCK = __import__('pathlib').Path(cls.temp.name)/'route.lock'
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), cls.module.Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.temp.cleanup()

    def request(self, path, body=None, auth=True):
        headers = {'Content-Type': 'application/json'}
        if auth:
            headers['Authorization'] = 'Bearer local-test-only-token'
        req = Request(f'http://127.0.0.1:{self.server.server_port}{path}', data=json.dumps(body).encode() if body is not None else None, headers=headers)
        try:
            with urlopen(req, timeout=3) as response:
                return response.status, json.load(response)
        except HTTPError as response:
            return response.code, json.load(response)

    def test_authentication_required(self):
        self.assertEqual(self.request('/jobs', auth=False)[0], 401)

    def test_coolify_return_blocks_android_creation_and_social_launch(self):
        m=self.module
        with patch.object(m,'coolify_return_state',return_value={'active':True}):
            self.assertFalse(m.ready())
            self.assertEqual(self.request('/brands',{'profile':'android-static'})[0],409)
            with patch.object(m,'urlopen') as launch:
                with self.assertRaises(RuntimeError):m.launch_social('bf-brand-one','instagram')
                launch.assert_not_called()

    def test_returned_sites_are_excluded_from_android_route_reconciliation(self):
        m=self.module;from pathlib import Path
        apps=[{'id':'bf-returned','domain':'returned.example.com','ip':'10.77.0.12','migrated':True,'returnedToCoolify':True}, {'id':'bf-pending','domain':'pending.example.com','ip':'10.77.0.13','migrated':True,'webPort':3000,'endpoints':[{'port':7700,'pathPrefixes':['/api','/health']}]}]
        route=Path(self.temp.name)/'return-routes.json'
        with patch.object(m,'android_inventory',return_value=apps),patch.object(m,'ROUTES',route):m.reconcile_routes()
        config=json.loads(route.read_text())['http']
        self.assertNotIn('bf-returned',config['routers']);self.assertNotIn('bf-returned',config['services'])
        self.assertEqual(config['services']['bf-pending-endpoint-0']['loadBalancer']['servers'],[{'url':'http://10.77.0.13:7700'}])

    def test_completed_return_does_not_emit_empty_traefik_maps(self):
        m=self.module
        route=m.Path(self.temp.name)/'return-empty-routes.json'
        apps=[{'id':'bf-returned','domain':'returned.example.com','migrated':True,'returnedToCoolify':True}]
        with patch.object(m,'android_inventory',return_value=apps),patch.object(m,'ROUTES',route):m.reconcile_routes()
        http=json.loads(route.read_text())['http']
        self.assertNotIn('routers',http)
        self.assertNotIn('services',http)
        self.assertIn('brandfleet-redirect',http['middlewares'])

    def test_completed_return_uses_retained_metadata_and_live_docker_without_pool(self):
        m=self.module
        android=[{'id':'bf-returned','domain':'returned.example.com','migrated':True,'returnedToCoolify':True,'state':'STOPPED'}]
        state={'active':True,'phase':'complete','retainedAndroid':android}
        raw=[{'Id':str(i)*64,'Name':'/container-'+str(i),'State':{'Running':running},'Config':{'Image':'current-image','Labels':{'traefik.http.routers.site.rule':'Host(`returned.example.com`)'}}}for i,running in [(1,True),(2,False)]]
        def command(argv,timeout=25):return ' '.join(d['Id']for d in raw)if argv[:2]==['docker','ps']else json.dumps(raw)
        with patch.object(m,'coolify_return_state',return_value=state),patch.object(m,'command',side_effect=command) as cmd,patch.object(m,'shared_services_inventory',return_value=([],{}, {'id':'shared-native'})),patch.object(m,'social_evidence_inventory') as social,patch.object(m,'pool_status') as pool,patch.object(m.Path,'read_text',return_value='MemTotal: 1024 kB\nMemAvailable: 512 kB\n'):
            self.assertEqual(m.android_inventory(),android)
            result=m.inventory()
            self.assertEqual([b['description']for b in result['brands']],['container-1'])
            self.assertEqual(result['brands'][0]['phase'],'coolify-production')
            self.assertFalse(result['capabilities']['create']);self.assertEqual(result['brands'][0]['actions'],[])
            self.assertEqual(result['mode'],'coolify-debian-return')
            social.assert_not_called();pool.assert_not_called()
            self.assertTrue(all(c.args[0][0]=='docker'for c in cmd.call_args_list))

    def test_accepted_shutdown_transition_uses_retained_inventory_without_pool(self):
        m=self.module
        app={'id':'bf-managed','domain':'managed.example.com','returnedToCoolify':True,'state':'STOPPED','coolifyDeployment':{'resourceUuid':'managed-uuid','primaryService':'web'}}
        state={'active':True,'phase':'accepted-awaiting-pool-shutdown','retainedAndroid':[app]}
        raw=[{'Id':'a'*64,'Name':'/web-managed-uuid','State':{'Running':True,'Pid':0},'Config':{'Image':'current-image','Labels':{'com.docker.compose.project':'managed-uuid','com.docker.compose.service':'web'}},'HostConfig':{}}]
        def command(argv,timeout=25):return raw[0]['Id']if argv[:2]==['docker','ps']else json.dumps(raw)
        with patch.object(m,'coolify_return_state',return_value=state),patch.object(m,'command',side_effect=command)as cmd,patch.object(m,'shared_services_inventory',return_value=([],{},{})),patch.object(m,'pool_status')as pool,patch.object(m.Path,'read_text',return_value='MemTotal: 1024 kB\nMemAvailable: 512 kB\n'):
            result=m.inventory()
        self.assertEqual(len(result['brands']),1)
        self.assertEqual(result['brands'][0]['status'],'running')
        self.assertFalse(result['capabilities']['create'])
        self.assertIn('shutdown is pending',result['notes'][0])
        self.assertNotIn('pool is stopped',result['notes'][0])
        pool.assert_not_called()
        self.assertTrue(all(c.args[0][0]=='docker'for c in cmd.call_args_list))

    def test_mail_admin_uses_fixed_helper_stdin_and_strips_private_fields(self):
        self.assertEqual(self.request('/mail-admin', {'action':'list'}, auth=False)[0],401)
        self.assertEqual(self.request('/mail-admin', {'action':'fixture_cleanup'})[0],400)
        from subprocess import CompletedProcess
        response={'ok':True,'changed':False,'state':{'domains':[]},'backup':'/private/path'}
        with patch.object(self.module.subprocess,'run',return_value=CompletedProcess([],0,json.dumps(response),'')) as run:
            status,data=self.request('/mail-admin',{'action':'list','command':'delete-all','path':'/etc/shadow'})
            self.assertEqual(status,200);self.assertNotIn('backup',data)
            self.assertEqual(run.call_args.args[0],['/usr/bin/python3','/opt/brandfleet/services/native-mail-admin.py'])
            self.assertEqual(json.loads(run.call_args.kwargs['input']),{'action':'list'})
            self.assertNotIn('shell',run.call_args.kwargs)

    def test_coolify_mapping_uses_current_roles_without_historical_duplicates(self):
        m=self.module
        app={'id':'bf-current','domain':'current.example.com','coolifyDeployment':{'resourceUuid':'site-uuid','containers':['web-old','db-site-uuid'],'primaryContainer':'web-old','primaryService':'web','consoleUrl':'https://console.example.com/service/site-uuid'}}
        def container(name,role,running):
            return {'Id':name+'-id','Name':'/'+name,'Created':'2026-10-07','State':{'Running':running,'Pid':0},'Config':{'Image':'retained-image','Labels':{'com.docker.compose.project':'site-uuid','com.docker.compose.service':role}},'HostConfig':{'Memory':128*1024*1024,'NanoCpus':500000000}}
        raw=[container('web-old','web',False),container('web-new','web',True),container('db-site-uuid','db',True)]
        result=m.coolify_brand(app,raw)
        self.assertEqual(result['status'],'running')
        self.assertEqual(result['website']['containerId'],'web-new-id')
        self.assertEqual(result['resources']['memoryLimitBytes'],256*1024*1024)
        self.assertEqual(result['resources']['cpuCores'],1)
        self.assertEqual(result['automation']['url'],app['coolifyDeployment']['consoleUrl'])
        raw[2]['State']['Running']=False
        self.assertEqual(m.coolify_brand(app,raw)['status'],'degraded')

    def test_completed_return_maps_compose_site_without_public_docker_labels(self):
        m=self.module
        app={'id':'bf-managed','domain':'managed.example.com','returnedToCoolify':True,'coolifyDeployment':{'resourceUuid':'managed-uuid','primaryService':'web'}}
        state={'active':True,'phase':'complete','retainedAndroid':[app]}
        raw=[{'Id':'a'*64,'Name':'/web-managed-uuid','State':{'Running':True,'Pid':0},'Config':{'Image':'current-image','Labels':{'com.docker.compose.project':'managed-uuid','com.docker.compose.service':'web'}},'HostConfig':{}}]
        def command(argv,timeout=25):return raw[0]['Id']if argv[:2]==['docker','ps']else json.dumps(raw)
        with patch.object(m,'coolify_return_state',return_value=state),patch.object(m,'command',side_effect=command),patch.object(m,'shared_services_inventory',return_value=([],{},{})),patch.object(m,'pool_status')as pool,patch.object(m.Path,'read_text',return_value='MemTotal: 1024 kB\nMemAvailable: 512 kB\n'):
            result=m.inventory()
        self.assertEqual(len(result['brands']),1)
        self.assertEqual(result['brands'][0]['id'],'bf-managed')
        self.assertEqual(result['brands'][0]['status'],'running')
        self.assertEqual(result['brands'][0]['phase'],'coolify-production')
        pool.assert_not_called()

    def test_unvalidated_profile_rejects_creation(self):
        with patch.object(self.module, 'ready', return_value=False):
            self.assertEqual(self.request('/brands', {'profile': 'android-static'})[0], 409)

    def test_disruptive_action_requires_matching_confirmation(self):
        current = {'brands': [{'id': 'bf-test', 'actions': ['remove']}]}
        with patch.object(self.module, 'inventory', return_value=current):
            self.assertEqual(self.request('/brands/bf-test/actions/remove', {'requestId': 'a'*32})[0], 400)

    def test_only_managed_ids_and_advertised_actions_can_execute(self):
        with patch.object(self.module, 'inventory', return_value={'brands': []}):
            self.assertEqual(self.request('/brands/bf-other/actions/remove', {'confirm': 'bf-other'})[0], 409)
        self.assertEqual(self.request('/brands/legacy-live/actions/remove', {})[0], 404)

    def test_create_forwards_fixed_argv_and_rejects_duplicate_domain(self):
        data = {'profile': 'android-static', 'name': 'App $(touch /tmp/never)', 'domain': 'new.example.com', 'requestId': 'a'*32, 'command': 'delete-all'}
        with patch.object(self.module, 'ready', return_value=True), patch.object(self.module, 'check_capacity'), patch.object(self.module, 'domain_in_use', return_value=False), patch.object(self.module, 'newjob', return_value={'id': 'test'}) as job:
            self.assertEqual(self.request('/brands', data)[0], 202)
            argv = job.call_args.args[3]
            self.assertEqual(argv[2], 'create')
            self.assertIn(data['name'], argv)
            self.assertNotIn(data['command'], argv)
        with patch.object(self.module, 'ready', return_value=True), patch.object(self.module, 'domain_in_use', return_value=True):
            self.assertEqual(self.request('/brands', data)[0], 409)

    def test_status_parses_runtime_result_shape(self):
        with patch.object(self.module.RUNTIME.__class__, 'exists', return_value=True), patch.object(self.module, 'command', return_value='{"ok":true,"result":[{"id":"bf-pilot","state":"RUNNING"}]}'):
            self.assertEqual(self.module.android_inventory()[0]['id'], 'bf-pilot')

    def test_only_display_may_use_stale_inventory(self):
        with patch.object(self.module.RUNTIME.__class__, 'exists', return_value=True), patch.object(self.module, 'command', return_value='{"result":[{"id":"bf-cached","migrated":true}]}'):
            self.module.android_inventory()
        with patch.object(self.module.RUNTIME.__class__, 'exists', return_value=True), patch.object(self.module, 'command', side_effect=TimeoutError('busy')):
            saved = self.module.android_inventory(allow_cached=True)
            self.assertEqual(saved[0]['id'], 'bf-cached')
            self.assertTrue(saved[0]['_inventoryStale'])
            with self.assertRaises(ValueError): self.module.android_inventory(required=True)

    def test_route_read_failure_keeps_published_routes(self):
        route=__import__('pathlib').Path(self.temp.name)/'retained-route.json'
        route.write_text('{"existing":"route"}')
        with patch.object(self.module, 'android_inventory', side_effect=ValueError('busy')), patch.object(self.module, 'ROUTES', route):
            with self.assertRaises(ValueError): self.module.reconcile_routes()
        self.assertEqual(route.read_text(), '{"existing":"route"}')

    def test_pool_capacity_reserves_memory_and_fails_closed(self):
        with patch.object(self.module, 'android_inventory', return_value=[]), patch.object(self.module, 'pool_status', return_value={'memoryAvailableBytes': 8*1024**3, 'maxInstances': 12}):
            self.module.check_capacity()
        with patch.object(self.module, 'android_inventory', return_value=[{'memoryMiB':2048}]*12), patch.object(self.module, 'pool_status', return_value={'memoryAvailableBytes': 8*1024**3, 'maxInstances': 12}):
            with self.assertRaises(ValueError): self.module.check_capacity()
        expanded={'memoryAvailableBytes':8*1024**3,'maxInstances':32,'assignedBudgetMiB':28672}
        with patch.object(self.module, 'android_inventory', return_value=[{'memoryMiB':2048}]*12), patch.object(self.module, 'pool_status', return_value=expanded):
            self.module.check_capacity()
        with patch.object(self.module, 'android_inventory', return_value=[{'memoryMiB':4096}]*7), patch.object(self.module, 'pool_status', return_value=expanded):
            with self.assertRaises(ValueError): self.module.check_capacity()
        with patch.object(self.module, 'android_inventory', return_value=[]), patch.object(self.module, 'pool_status', return_value={**expanded,'memoryAvailableBytes':4*1024**3}):
            with self.assertRaises(ValueError): self.module.check_capacity()

    def test_migrated_routes_override_legacy_and_keep_api_backend(self):
        apps=[{'id':'bf-db','domain':'db.example.com','aliases':['www.db.example.com'],'ip':'10.77.0.12','migrated':True,'webPort':3000,'endpoints':[{'port':7700,'pathPrefixes':['/api','/admin','/health']}]}, {'id':'bf-unready','domain':'pending.example.com','ip':'10.77.0.13','state':'RUNNING','ready':False}]
        route=__import__('pathlib').Path(self.temp.name)/'routes.json'
        with patch.object(self.module,'android_inventory',return_value=apps),patch.object(self.module,'ROUTES',route):
            self.module.reconcile_routes()
        config=json.loads(route.read_text())['http']
        self.assertGreater(config['routers']['bf-db']['priority'],10000)
        self.assertEqual(config['services']['bf-db']['loadBalancer']['servers'][0]['url'],'http://10.77.0.12:3000')
        self.assertIn('PathPrefix(`/api`)',config['routers']['bf-db-endpoint-0']['rule'])
        self.assertGreater(config['routers']['bf-db-endpoint-0']['priority'],config['routers']['bf-db']['priority'])
        self.assertEqual(config['services']['bf-db-endpoint-0']['loadBalancer']['servers'][0]['url'],'http://10.77.0.12:7700')
        self.assertNotIn('bf-unready',config['routers'])
        with patch.object(self.module, 'android_inventory', return_value=[]), patch.object(self.module, 'pool_status', side_effect=ValueError('unavailable')):
            with self.assertRaises(ValueError): self.module.check_capacity()

    def test_parking_route_cannot_override_real_brand(self):
        apps=[{'id':'bf-domain-parking','domain':'example.com','ip':'10.77.0.26','migrated':True,'webPort':8080},{'id':'bf-real-app','domain':'example.com','ip':'10.77.0.27','migrated':True,'webPort':3000}]
        route=__import__('pathlib').Path(self.temp.name)/'parking-routes.json'
        with patch.object(self.module,'android_inventory',return_value=apps),patch.object(self.module,'ROUTES',route):self.module.reconcile_routes()
        routers=json.loads(route.read_text())['http']['routers']
        self.assertLess(routers['bf-domain-parking']['priority'],routers['bf-real-app']['priority'])

    def test_retired_migrated_cells_publish_no_routes_and_do_not_consume_capacity(self):
        m=self.module;from pathlib import Path
        active={'id':'bf-brand-one','domain':'brand-one.example.com','ip':'10.77.0.12','migrated':True,'memoryMiB':1536,'webPort':3000}
        retired=[{'id':name,'domain':domain,'ip':'10.77.0.'+str(i),'migrated':True,'retired':True,'memoryMiB':1536,'webPort':8080}for i,(name,domain)in enumerate([('bf-retired-chat','retired-chat.example.com'),('bf-retired-landing','retired-landing.example.com'),('bf-dns-editor','dns.example.com')],23)]
        route=Path(self.temp.name)/'retired-routes.json'
        with patch.object(m,'android_inventory',return_value=[active,*retired]),patch.object(m,'ROUTES',route):m.reconcile_routes()
        config=json.loads(route.read_text())['http']
        self.assertEqual(set(config['routers']),{'bf-brand-one','bf-brand-one-http'})
        self.assertEqual(config['services']['bf-brand-one']['loadBalancer']['servers'],[{'url':'http://10.77.0.12:3000'}])
        with patch.object(m,'android_inventory',return_value=[active,*retired]),patch.object(m,'pool_status',return_value={'memoryAvailableBytes':8*1024**3,'maxInstances':2,'assignedBudgetMiB':4096}):m.check_capacity()

    def test_retired_domains_do_not_reappear_as_legacy_or_android_brand_controls(self):
        m=self.module
        retired=[{'id':name,'domain':domain,'ip':'10.77.0.'+str(i),'migrated':True,'retired':True,'state':'STOPPED'}for i,(name,domain)in enumerate([('bf-retired-chat','retired-chat.example.com'),('bf-retired-landing','retired-landing.example.com'),('bf-dns-editor','dns.example.com')],23)]
        raw=[{'Id':str(i)*64,'Name':'/retained-source-'+str(i),'State':{'Running':False},'Config':{'Image':'retained-source','Labels':{'traefik.http.routers.old.rule':'Host(`'+a['domain']+'`)'}}}for i,a in enumerate(retired,1)]
        active={'id':'bf-brand-one','domain':'brand-one.example.com','ip':'10.77.0.12','migrated':True,'nativeRuntime':True,'state':'RUNNING','bootCompleted':True,'webHealthy':True,'memoryMiB':1536}
        def command(argv,timeout=25):return ' '.join(d['Id']for d in raw)if argv[:2]==['docker','ps']else json.dumps(raw)
        with patch.object(m,'command',side_effect=command),patch.object(m,'shared_services_inventory',return_value=([],{},{})),patch.object(m,'android_inventory',return_value=[*retired,active]),patch.object(m,'social_evidence_inventory',return_value={}),patch.object(m,'pool_status',return_value={'memoryAvailableBytes':8*1024**3,'memoryTotalBytes':16*1024**3,'cpuCount':4,'maxInstances':2,'assignedBudgetMiB':4096}),patch.object(m,'ready',return_value=True),patch.object(m.Path,'read_text',return_value='MemTotal: 1024 kB\nMemAvailable: 512 kB\n'):result=m.inventory()
        self.assertEqual([b['id']for b in result['brands']],['bf-brand-one'])
        self.assertEqual(result['brands'][0]['android']['screenUrl'],'https://fleet.example.com/android/view/bf-brand-one')
        self.assertIn('stop',result['brands'][0]['actions']);self.assertTrue(result['capabilities']['create'])

    def test_native_dns_management_has_shared_status_and_same_public_url(self):
        m=self.module
        measured={'units':{'brandfleet-dns-editor':{'ActiveState':'active','MemoryCurrent':'33554432'}},'apps':{},'workspace':{},'appsMeasured':False,'measurementAvailable':True,'outboundEnabled':None,'measuredAt':'2026-10-06T13:00:00Z'}
        route=lambda name,host,port=8080:name=='brandfleet-native-dns.yaml' and host=='dns.example.com' and port==8089
        with patch.object(m,'_shared_measure',return_value=measured),patch.object(m,'_shared_import',return_value=False),patch.object(m,'_shared_route',side_effect=route),patch.object(m,'_shared_json',return_value={}):services,_,_=m.shared_services_inventory([])
        dns=next(s for s in services if s['id']=='dns-editor')
        self.assertEqual(dns['status'],'running');self.assertEqual(dns['deployment'],'production');self.assertEqual(dns['url'],'https://dns.example.com/');self.assertEqual(dns['resources']['memoryBytes'],33554432)
        self.assertNotIn('android',dns);self.assertIn('no Android device',dns['detail'])

    def test_mail_protocol_fields_and_quarantine_use_fixed_helper(self):
        from subprocess import CompletedProcess
        response={'ok':True,'messages':[],'total':0,'private_path':'/private/mail','backup':'/private/backup'}
        with patch.object(self.module.subprocess,'run',return_value=CompletedProcess([],0,json.dumps(response),''))as run:
            status,data=self.request('/mail-admin',{'action':'quarantine_release','id':'a'*64,'recipient':'override@example.com','command':'delete-all'})
            self.assertEqual(status,200);self.assertNotIn('private_path',data);self.assertNotIn('backup',data)
            self.assertEqual(json.loads(run.call_args.kwargs['input']),{'action':'quarantine_release','id':'a'*64})
            self.request('/mail-admin',{'action':'mailbox_update','email':'dev@example.com','protocols':{'imap':False,'smtp':True},'tls_enforce_in':True,'tls_enforce_out':False})
            self.assertEqual(json.loads(run.call_args.kwargs['input'])['protocols'],{'imap':False,'smtp':True})

    def test_native_service_measurement_cache_expires_and_failure_is_unknown(self):
        m=self.module; m.SHARED_CACHE.update(at=0,value=None)
        def reading(argv,timeout=25):
            if 'brandfleet-turn-ingress.service' in argv:return 'active'
            if 'systemctl' in argv:return 'MemoryCurrent=4096\nId=apache2.service\nActiveState=active\n'
            if 'app:list' in argv:return '{"enabled":{"files":"2.4.0"}}'
            if 'status' in argv:return '{"installed":true,"maintenance":false,"needsDbUpgrade":false}'
            return 'smtp'
        with patch.object(m,'command',side_effect=reading) as cmd:
            first=m._shared_measure();self.assertEqual(first['apps']['files'],'2.4.0');self.assertTrue(first['turnIngressActive']);self.assertEqual(m._shared_measure(),first);self.assertEqual(cmd.call_count,5)
        m.SHARED_CACHE['at']-=46
        with patch.object(m,'command',side_effect=TimeoutError('unavailable')):
            failed=m._shared_measure();self.assertEqual(failed['apps'],{});self.assertFalse(failed['appsMeasured']);self.assertEqual(m._shared_status(failed,('apache2',)),'unknown');self.assertIsNone(failed['outboundEnabled'])
        m.SHARED_CACHE.update(at=0,value=None)

    def test_turn_production_requires_owned_live_ingress_and_public_media_proof(self):
        from pathlib import Path
        m=self.module;private=Path(self.temp.name)/'turn';stage=private/'turn-public-validation';stage.mkdir(parents=True,exist_ok=True)
        (stage/'public-activation-proof.json').write_text(json.dumps({'passed':True,'originalRetainedStopped':True}))
        (private/'turn-ingress-owned-rules.json').write_text(json.dumps({'owned':[0,1]}))
        wan={'passed':2,'tests':[{'passed':True},{'passed':True}],'publicMediaTestRun':True,'wanNATActivated':True,'allocatedSessionsReleased':True}
        states=[{'Name':'/legacy-turn','State':{'Running':False}}]
        with patch.object(m,'SHARED_PRIVATE',private):
            self.assertFalse(m._shared_turn_public({'turnIngressActive':True},states))
            (stage/'public-wan-validation.json').write_text(json.dumps(wan))
            self.assertTrue(m._shared_turn_public({'turnIngressActive':True},states))
            self.assertFalse(m._shared_turn_public({'turnIngressActive':None},states))
            self.assertFalse(m._shared_turn_public({'turnIngressActive':True},[{'Name':'/legacy-turn','State':{'Running':True}}]))
            wan['publicMediaTestRun']=False;(stage/'public-wan-validation.json').write_text(json.dumps(wan))
            self.assertFalse(m._shared_turn_public({'turnIngressActive':True},states))

    def test_native_publication_requires_current_stopped_source_and_correct_route(self):
        m=self.module;from pathlib import Path
        private=Path(self.temp.name)/'shared';snapshot=private/'workspace-final-production-test';snapshot.mkdir(parents=True,exist_ok=True)
        (snapshot/'manifest.json').write_text(json.dumps({'source_writers_frozen':True}))
        (private/'workspace-final-import-proof.json').write_text(json.dumps({'source_frozen_snapshot':str(snapshot)}))
        states=[{'Name':'/legacy-workspace-app-1','State':{'Running':False}},{'Name':'/legacy-workspace-cron-1','State':{'Running':False}}]
        with patch.object(m,'SHARED_PRIVATE',private):
            self.assertTrue(m._shared_import('workspace',states));states[0]['State']['Running']=True;self.assertFalse(m._shared_import('workspace',states));self.assertFalse(m._shared_import('mail',states))
        route=private/'brandfleet-native-mail.yaml';route.write_text('rule: Host(`mail.example.com`)\nurl: http://10.77.2.50:8080')
        with patch.object(m,'ROUTES',private/'apps.yaml'):
            self.assertTrue(m._shared_route(route.name,'mail.example.com'));self.assertFalse(m._shared_route(route.name,'other.example'));self.assertFalse(m._shared_route(route.name,'mail.example.com',3000))

    def test_native_launcher_never_reports_missing_measurements_ready(self):
        m=self.module;measured={'units':{},'apps':{},'workspace':{},'appsMeasured':False,'measurementAvailable':False,'outboundEnabled':None,'measuredAt':'2026-10-06T07:00:00Z'}
        with patch.object(m,'_shared_measure',return_value=measured),patch.object(m,'_shared_import',return_value=False),patch.object(m,'_shared_route',return_value=False),patch.object(m,'_shared_json',return_value={}):
            services,workspace,infra=m.shared_services_inventory([])
        self.assertTrue(all(s['status']=='unknown' for s in services));self.assertTrue(all(a['status']=='unknown' and not a['url'] for a in workspace['apps']));self.assertEqual(workspace['deployment'],'unknown');self.assertEqual(workspace['retainedGoogleDomains'],['google-one.example.com','google-two.example.com']);self.assertEqual(infra['status'],'unknown')

    def test_legacy_shared_cards_hidden_only_for_exact_verified_production_hosts(self):
        m=self.module
        domains=['mail.example.com','workspace.example.com','email-api.example.com','other.portfolio-brand.example.com']
        raw=[{'Id':str(i)*64,'Name':'/source-'+str(i),'State':{'Running':False},'Config':{'Image':'retained-source','Labels':{'traefik.http.routers.source.rule':'Host(`'+domain+'`)'}}} for i,domain in enumerate(domains,1)]
        # A mixed-host source must remain visible until every exact host has a verified replacement.
        raw.append({'Id':'5'*64,'Name':'/mixed','State':{'Running':False},'Config':{'Image':'retained-source','Labels':{'traefik.http.routers.mixed.rule':'Host(`mail.example.com`) || Host(`unverified.portfolio-brand.example.com`)'}}})
        def command(argv,timeout=25):return ' '.join(d['Id'] for d in raw) if argv[:2]==['docker','ps'] else json.dumps(raw)
        for published in (False,True):
            services=[{'id':kind,'deployment':'production' if published else 'preview'} for kind in ('mail','workspace','freeresend')]
            with self.subTest(published=published),patch.object(m,'command',side_effect=command),patch.object(m,'android_inventory',return_value=[]),patch.object(m,'shared_services_inventory',return_value=(services,{}, {'id':'shared-native'})) as shared,patch.object(m,'ready',return_value=False),patch.object(m,'pool_status',side_effect=RuntimeError('unavailable')),patch.object(m.Path,'read_text',return_value='MemTotal: 1024 kB\nMemAvailable: 512 kB\n'):
                current=m.inventory()
                shared.assert_called_once_with(raw)
            self.assertEqual([b['description'] for b in current['brands']],['source-4','mixed'] if published else ['source-1','source-2','source-3','source-4','mixed'])
            self.assertEqual(current['services'],services)


    def test_package_evidence_requires_six_verified_packages_and_strips_unknown_fields(self):
        m=self.module
        raw={'evidencePresent':True,'historicalEvidence':True,'packageInstallationAccepted':True,'lastVerified':'2026-10-06T08:19:43Z','accountLoginAttempted':False,'VPNActivated':False,'password':'never-display-password','packages':[{'package':package,'app':'verified','versionCode':1,'versionName':'1.0','uid':10080,'installedAPKHashesVerified':True,'forceStopped':True,'downloadURL':'https://private.example/?token=never-display-token'} for package in m.SOCIAL_PACKAGES]}
        result=m.sanitize_package_evidence(raw)
        self.assertTrue(result['packageInstallationAccepted']);self.assertTrue(result['historicalEvidence']);self.assertFalse(result['accountLoginAttempted']);self.assertFalse(result['VPNActivated']);self.assertNotIn('never-display',json.dumps(result))
        raw['packages'].append({'package':'arbitrary.injected','installedAPKHashesVerified':True})
        self.assertEqual(len(m.sanitize_package_evidence(raw)['packages']),6)
        raw['packages'][0]['installedAPKHashesVerified']='true'
        self.assertFalse(m.sanitize_package_evidence(raw)['packageInstallationAccepted'])
        raw['lastVerified']='not-a-date';self.assertFalse(m.sanitize_package_evidence(raw)['evidencePresent'])
        self.assertEqual(m.sanitize_package_evidence(raw)['packages'],[])

    def test_social_reader_fixed_argv_and_cache_has_no_live_package_calls(self):
        m=self.module;data={'readOnly':True,'devices':[{'id':'bf-test','lastVerified':'2026-10-06T08:19:43Z','evidencePresent':True,'historicalEvidence':True,'packages':[]}]}
        with patch.dict(m.SOCIAL_CACHE,{'at':0,'value':None}),patch.object(m,'command',return_value=json.dumps(data)) as cmd:
            self.assertIn('bf-test',m.social_evidence_inventory());m.social_evidence_inventory();cmd.assert_called_once_with(['/usr/bin/python3',str(m.RUNTIME),'social-evidence','--json'],timeout=6)
        import runpy,subprocess,sys
        wrapper=os.path.join(os.path.dirname(__file__),'pool-runtime.py')
        with patch.object(sys,'argv',[wrapper,'social-evidence','--json']),patch.object(subprocess,'run',return_value=subprocess.CompletedProcess([],0)) as run:
            with self.assertRaises(SystemExit) as stopped:runpy.run_path(wrapper,run_name='__main__')
            self.assertEqual(stopped.exception.code,0);self.assertEqual(run.call_args.kwargs['timeout'],5)
            self.assertEqual(run.call_args.args[0][-1],'/usr/bin/python3 /opt/brandfleet/android-runtime/social-evidence.py --json')
        with patch.object(sys,'argv',[wrapper,'social-evidence','--json','--command=arbitrary']),patch.object(subprocess,'run') as run:
            with self.assertRaises(SystemExit):runpy.run_path(wrapper,run_name='__main__')
            run.assert_not_called()

    def test_social_launch_authentication_body_and_fixed_service_contract(self):
        from datetime import datetime, timezone
        from unittest.mock import MagicMock
        m=self.module
        self.assertEqual(self.request('/brands/bf-test/social/launch',{'app':'instagram'},auth=False)[0],401)
        with patch.object(m,'launch_social') as launch:
            self.assertEqual(self.request('/brands/bf-test/social/launch',{'app':'instagram','command':'id'})[0],400)
            launch.assert_not_called()
        response=MagicMock();response.__enter__.return_value=response;response.status=200
        raw={'ok':True,'app':'instagram','package':'com.instagram.android','foregroundConfirmed':True,'foregroundPackage':'com.instagram.android','launchedAt':datetime.now(timezone.utc).isoformat(),'viewerUrl':'/android/view/bf-test','private':'do-not-return'}
        response.read.return_value=json.dumps(raw).encode()
        with patch.object(m,'ready',return_value=True),patch.object(m,'urlopen',return_value=response) as upstream,patch.object(m,'newjob') as lifecycle:
            status,data=self.request('/brands/bf-test/social/launch',{'app':'instagram'})
            self.assertEqual(status,200);self.assertNotIn('private',data);self.assertTrue(data['foregroundConfirmed'])
            req=upstream.call_args.args[0];self.assertEqual(req.full_url,'http://127.0.0.1:9002/launch/bf-test');self.assertEqual(json.loads(req.data),{'app':'instagram'});self.assertEqual(upstream.call_args.kwargs['timeout'],25)
            lifecycle.assert_not_called()
        with patch.object(m,'ready',return_value=True),patch.object(m,'urlopen') as upstream:
            self.assertEqual(self.request('/brands/bf-test/social/launch',{'app':'arbitrary/.Command'})[0],400)
            upstream.assert_not_called()

    def test_social_launch_fails_closed_on_stale_or_mismatched_confirmation(self):
        from datetime import datetime, timezone
        from unittest.mock import MagicMock
        m=self.module
        raw={'ok':True,'app':'whatsapp','package':'com.whatsapp','foregroundConfirmed':True,'foregroundPackage':'com.whatsapp','launchedAt':datetime.now(timezone.utc).isoformat(),'viewerUrl':'/android/view/bf-test'}
        for changed in ({'package':'com.other'},{'viewerUrl':'https://external.invalid'},{'foregroundPackage':'com.other'},{'launchedAt':'2020-01-01T00:00:00Z'}):
            response=MagicMock();response.__enter__.return_value=response;response.status=200;response.read.return_value=json.dumps({**raw,**changed}).encode()
            with self.subTest(changed=changed),patch.object(m,'ready',return_value=True),patch.object(m,'urlopen',return_value=response):
                self.assertEqual(self.request('/brands/bf-test/social/launch',{'app':'whatsapp'})[0],503)
        with patch.object(m,'ready',return_value=True),patch.object(m,'urlopen',side_effect=HTTPError('fixed',409,'busy',{},None)):
            status,data=self.request('/brands/bf-test/social/launch',{'app':'whatsapp'});self.assertEqual(status,409);self.assertIn('retry',data['error'])
        for upstream_code,expected in ((400,400),(503,503)):
            with self.subTest(upstream_code=upstream_code),patch.object(m,'ready',return_value=True),patch.object(m,'urlopen',side_effect=HTTPError('fixed',upstream_code,'private diagnostic',{},None)):
                status,data=self.request('/brands/bf-test/social/launch',{'app':'whatsapp'});self.assertEqual(status,expected);self.assertNotIn('private',data['error'])
        with patch.object(m,'ready',return_value=False),patch.object(m,'urlopen') as upstream:
            self.assertEqual(self.request('/brands/bf-test/social/launch',{'app':'whatsapp'})[0],503);upstream.assert_not_called()

if __name__ == '__main__':
    unittest.main()
