#!/usr/bin/python3
"""Verify a pinned official Redroid OCI cache without downloading or mutating it."""
import argparse, hashlib, json, pathlib, re

DEFAULT_IMAGE='docker.io/redroid/redroid@sha256:0a611199ba2e0b5d60af39b3327a517f6407231f4352114ed3bd3cbfe2be69aa'
IMAGE_RE=re.compile(r'^docker\.io/redroid/redroid@sha256:([0-9a-f]{64})$')

def require(ok,message):
 if not ok:raise ValueError(message)

def load(path):
 require(path.is_file() and not path.is_symlink() and path.stat().st_size<10*1024*1024,'Missing or unsafe image attestation')
 return json.loads(path.read_bytes())

def sha(path):
 digest=hashlib.sha256()
 with path.open('rb') as stream:
  for block in iter(lambda:stream.read(1024*1024),b''):digest.update(block)
 return 'sha256:'+digest.hexdigest()

def descriptor_blob(cache,descriptor):
 digest=descriptor.get('digest','');require(re.fullmatch(r'sha256:[0-9a-f]{64}',digest) is not None,'Image descriptor must use SHA256')
 path=cache/'blobs'/'sha256'/digest.split(':')[1]
 require(path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(cache.resolve()),'Image blob leaves its cache')
 require(path.stat().st_size==descriptor.get('size'),'Image blob size mismatch')
 require(sha(path)==digest,'Image blob digest mismatch')
 return path

def verify(image,cache=None,base=pathlib.Path('/opt/brandfleet/android/images')):
 match=IMAGE_RE.fullmatch(image);require(match is not None,'Image must pin the official Redroid repository by SHA256')
 digest='sha256:'+match[1]
 cache=pathlib.Path(cache) if cache else base/('redroid14' if image==DEFAULT_IMAGE else 'digest-'+match[1])
 require(cache.is_dir() and not cache.is_symlink() and cache.resolve().parent==base.resolve(),'Image cache must be an immediate owned images directory')
 require(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}',cache.name) is not None,'Image cache name must be safe for fixed command transport')
 index=load(cache/'index.json');require(index.get('schemaVersion')==2,'Unsupported OCI index')
 manifests=index.get('manifests',[])
 selected=[x for x in manifests if x.get('annotations',{}).get('org.opencontainers.image.ref.name')=='base']
 if not selected and len(manifests)==1:selected=manifests
 require(len(selected)==1,'OCI cache requires one unambiguous base reference')
 descriptor=selected[0];manifest_path=descriptor_blob(cache,descriptor);manifest=load(manifest_path)
 require(manifest.get('schemaVersion')==2 and 'config' in manifest and 'layers' in manifest,'OCI base must be an image manifest')
 config=load(descriptor_blob(cache,manifest['config']))
 require(config.get('architecture')=='amd64' and config.get('os')=='linux','Image requires amd64 Linux')
 for layer in manifest['layers']:descriptor_blob(cache,layer)
 source_path=cache/'source.json'
 if not source_path.is_file() and image==DEFAULT_IMAGE:source_path=base/'redroid14-source.json'
 source=load(source_path)
 require(source.get('Digest')==digest and source.get('Architecture')=='amd64' and source.get('Os')=='linux','Source identity differs from requested pinned image')
 raw=cache/'registry-manifest.json'
 if raw.is_file():
  require(sha(raw)==digest,'Registry manifest differs from requested source digest');registry=load(raw)
  if 'manifests' in registry:
   candidates=[x for x in registry['manifests'] if x.get('platform',{}).get('architecture')=='amd64' and x.get('platform',{}).get('os')=='linux']
   require(len(candidates)==1,'Registry index requires one amd64 Linux platform')
   child=candidates[0]
   if child['digest']==descriptor['digest']:
    require(child.get('size')==descriptor.get('size'),'Registry platform manifest size differs');original=manifest
   else:
    platform=cache/'registry-platform-manifest.json'
    require(platform.is_file() and platform.stat().st_size==child.get('size') and sha(platform)==child['digest'],'Converted OCI cache requires the original pinned platform manifest')
    original=load(platform)
  else:original=registry
  def chain(obj):return [(x.get('digest'),x.get('size')) for x in [obj['config'],*obj['layers']]]
  require(chain(original)==chain(manifest),'Source and OCI configuration/layer chains differ')
  provenance='pinned-registry-manifest-config-layer-chain'
 else:
  require(image==DEFAULT_IMAGE,'Non-default images require the pinned raw registry manifest')
  require(source.get('Layers')==[x['digest'] for x in manifest['layers']],'Legacy image layers differ from source inspection')
  provenance='legacy-source-inspection-layer-chain'
 return {'cache':str(cache),'sourceImage':image,'sourceDigest':digest,'ociManifestDigest':descriptor['digest'],'configDigest':manifest['config']['digest'],'architecture':config['architecture'],'os':config['os'],'created':config.get('created'),'attestation':provenance}

def main():
 p=argparse.ArgumentParser();p.add_argument('--image',required=True);p.add_argument('--oci-dir');p.add_argument('--format',choices=('json','tsv'),default='json');a=p.parse_args()
 info=verify(a.image,a.oci_dir)
 if a.format=='tsv':print('\t'.join(info[k] for k in ('cache','sourceDigest','ociManifestDigest')))
 else:print(json.dumps(info,indent=2))

if __name__=='__main__':main()
