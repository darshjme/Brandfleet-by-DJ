#!/usr/bin/python3
"""One fixed read-only fleet evidence snapshot. No Android/ADB commands."""
import argparse,datetime,json,pathlib
ROOT=pathlib.Path('/opt/brandfleet/social-validation/fleet')
CAT=pathlib.Path('/opt/brandfleet/social-apks/verified-candidates.json')
IDS={'bf-brand-one','bf-brand-two'}
APPS={'figma-mirror','whatsapp','x','linkedin-global','tailscale','instagram-native'}
def snapshot():
 catalog={r['id']:r for r in json.loads(CAT.read_text())if r['id']in APPS}
 devices=[]
 for ident in sorted(IDS):
  path=ROOT/(ident+'.json')
  try:
   if path.stat().st_size>1024*1024:raise ValueError('Evidence exceeds fixed size limit')
   raw=json.loads(path.read_text());assert raw['id']==ident
   packages=[]
   for row in raw.get('packages',[]):
    if row.get('app')not in APPS:continue
    pinned=catalog[row['app']]
    if row.get('package')!=pinned['package']:continue
    packages.append({k:row.get(k)for k in ['app','package','versionCode','uid','installedAPKHashesVerified','forceStopped']}|{'versionName':pinned['versionName'],'publisherVerification':pinned['publisherVerification']})
   complete=raw.get('passed')is True and raw.get('backendContinuity')is True and {r['app']for r in packages}==APPS and all(r['installedAPKHashesVerified']is True and r['forceStopped']is True and isinstance(r['uid'],int)and r['uid']>=10000 for r in packages)
   devices.append({'id':ident,'evidencePresent':True,'packageInstallationAccepted':complete,'lastVerified':raw.get('finishedAt'),'packages':packages,'backendContinuity':raw.get('backendContinuity',False),'accountLoginAttempted':raw.get('accountLoginAttempted',False),'VPNActivated':raw.get('VPNActivated',False),'historicalEvidence':True})
  except(FileNotFoundError,ValueError,KeyError,AssertionError):devices.append({'id':ident,'evidencePresent':False,'packageInstallationAccepted':False,'packages':[],'historicalEvidence':True})
 return {'at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'readOnly':True,'devices':devices,'acceptedCount':sum(d['packageInstallationAccepted']for d in devices),'plannedCount':len(IDS),'limitations':['Figma is the official companion, not the full editor.','Custom-ROM support must be checked on your own device.','Package installation and bounded pilots do not prove account compatibility or automation.','Tailscale sign-in, an approved home exit node and per-app VPN routing require separate setup.']}
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--json',action='store_true',required=True);p.parse_args();print(json.dumps(snapshot()))
