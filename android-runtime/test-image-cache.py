#!/usr/bin/python3
"""Exact digest/cache provenance regression tests; no real image downloads."""
import hashlib, importlib.util, json, pathlib, tempfile, unittest

spec=importlib.util.spec_from_file_location('image_cache',pathlib.Path(__file__).with_name('image-cache.py'))
cache_api=importlib.util.module_from_spec(spec);spec.loader.exec_module(cache_api)

class ImageCacheTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.base=pathlib.Path(self.tmp.name)/'images';self.base.mkdir()
 def tearDown(self):self.tmp.cleanup()
 def encoded(self,value):return json.dumps(value,separators=(',',':')).encode()
 def blob(self,cache,data,media='test'):
  digest=hashlib.sha256(data).hexdigest();path=cache/'blobs/sha256'/digest
  path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
  return {'digest':'sha256:'+digest,'size':len(data),'mediaType':media}
 def fixture(self,name='staged16',arch='amd64',registry_index=True,converted=False):
  cache=self.base/name;cache.mkdir()
  config=self.blob(cache,self.encoded({'architecture':arch,'os':'linux','created':'2025-06-27T00:00:00Z','fixtureImage':name}))
  layer=self.blob(cache,b'complete compressed image layer')
  obj={'schemaVersion':2,'config':config,'layers':[layer]}
  manifest=self.blob(cache,self.encoded(obj))
  descriptor=dict(manifest,annotations={'org.opencontainers.image.ref.name':'base'})
  (cache/'index.json').write_bytes(self.encoded({'schemaVersion':2,'manifests':[descriptor]}))
  original=dict(obj,mediaType='registry-original') if converted else obj
  raw_platform=self.encoded(original)
  child={'digest':'sha256:'+hashlib.sha256(raw_platform).hexdigest(),'size':len(raw_platform),'platform':{'architecture':arch,'os':'linux'}}
  raw=self.encoded({'schemaVersion':2,'manifests':[child]}) if registry_index else raw_platform
  digest='sha256:'+hashlib.sha256(raw).hexdigest();image='docker.io/redroid/redroid@'+digest
  (cache/'registry-manifest.json').write_bytes(raw)
  if converted:(cache/'registry-platform-manifest.json').write_bytes(raw_platform)
  (cache/'source.json').write_bytes(self.encoded({'Digest':digest,'Architecture':'amd64','Os':'linux','Layers':[layer['digest']]}))
  return image,cache,obj
 def test_exact_index_platform_and_layers(self):
  image,cache,_=self.fixture();out=cache_api.verify(image,cache,self.base)
  self.assertEqual(out['sourceImage'],image);self.assertEqual(out['cache'],str(cache))
  self.assertEqual(out['attestation'],'pinned-registry-manifest-config-layer-chain')
 def test_default_legacy_cache_preserves_verified_layers(self):
  _,cache,obj=self.fixture('redroid14');(cache/'registry-manifest.json').unlink()
  (cache/'source.json').unlink()
  (self.base/'redroid14-source.json').write_bytes(self.encoded({'Digest':cache_api.DEFAULT_IMAGE.rsplit('@',1)[1],'Architecture':'amd64','Os':'linux','Layers':[obj['layers'][0]['digest']]}))
  out=cache_api.verify(cache_api.DEFAULT_IMAGE,base=self.base)
  self.assertEqual(out['cache'],str(cache));self.assertEqual(out['attestation'],'legacy-source-inspection-layer-chain')
 def test_single_platform_manifest(self):
  image,cache,_=self.fixture(registry_index=False);self.assertEqual(cache_api.verify(image,cache,self.base)['sourceImage'],image)
 def test_converted_media_types_require_same_config_and_layers(self):
  image,cache,_=self.fixture(converted=True);cache_api.verify(image,cache,self.base)
  (cache/'registry-platform-manifest.json').unlink()
  with self.assertRaisesRegex(ValueError,'original pinned'):cache_api.verify(image,cache,self.base)
 def test_explicit_new_image_never_uses_legacy_cache(self):
  old,legacy,_=self.fixture('redroid14');new,newcache,_=self.fixture('staged16')
  with self.assertRaisesRegex(ValueError,'Source identity'):cache_api.verify(new,legacy,self.base)
  self.assertEqual(cache_api.verify(new,newcache,self.base)['sourceImage'],new)
 def test_new_default_cache_is_digest_scoped(self):
  image,cache,_=self.fixture();target=self.base/('digest-'+image.rsplit(':',1)[1]);cache.rename(target)
  self.assertEqual(cache_api.verify(image,base=self.base)['cache'],str(target))
 def test_nondefault_requires_registry_attestation(self):
  image,cache,_=self.fixture();(cache/'registry-manifest.json').unlink()
  with self.assertRaisesRegex(ValueError,'raw registry'):cache_api.verify(image,cache,self.base)
 def test_tampered_layer_rejected(self):
  image,cache,obj=self.fixture();path=cache/'blobs/sha256'/obj['layers'][0]['digest'].split(':')[1]
  path.write_bytes(b'X'*path.stat().st_size)
  with self.assertRaisesRegex(ValueError,'digest mismatch'):cache_api.verify(image,cache,self.base)
 def test_incomplete_cache_rejected(self):
  image,cache,obj=self.fixture();(cache/'blobs/sha256'/obj['config']['digest'].split(':')[1]).unlink()
  with self.assertRaisesRegex(ValueError,'Image blob'):cache_api.verify(image,cache,self.base)
 def test_wrong_cpu_architecture_rejected(self):
  image,cache,_=self.fixture(arch='arm64')
  with self.assertRaisesRegex(ValueError,'amd64 Linux'):cache_api.verify(image,cache,self.base)
 def test_tag_or_other_repository_rejected(self):
  for image in ('docker.io/redroid/redroid:16.0.0-latest','docker.io/evil/redroid@sha256:'+'f'*64):
   with self.assertRaisesRegex(ValueError,'official Redroid'):cache_api.verify(image,base=self.base)
 def test_external_or_symlink_cache_rejected(self):
  image,cache,_=self.fixture();alias=self.base/'alias';alias.symlink_to(cache,target_is_directory=True)
  with self.assertRaisesRegex(ValueError,'immediate owned'):cache_api.verify(image,alias,self.base)
  with self.assertRaisesRegex(ValueError,'immediate owned'):cache_api.verify(image,self.base,self.base)
 def test_registry_pin_tamper_rejected(self):
  image,cache,_=self.fixture();(cache/'registry-manifest.json').write_bytes(b'{}')
  with self.assertRaisesRegex(ValueError,'requested source digest'):cache_api.verify(image,cache,self.base)

if __name__=='__main__':unittest.main()
