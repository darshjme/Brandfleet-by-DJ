#!/usr/bin/python3
import json,subprocess,sys
from pathlib import Path
assert Path('/etc/hostname').read_text().strip()=='bf-services'
source=json.loads(Path('/root/brandfleet-private/sender-ownership.json').read_text())['ownership']
delegated_path=Path('/root/brandfleet-private/freeresend-smtp.json')
delegated=json.loads(delegated_path.read_text()) if delegated_path.exists() else None
delegated_lua='{}'
if delegated:
 assert delegated['domains']==['vpn-brand.example.com','mail-brand.example.com','calendar-brand.example.com','trading-brand.example.com']
 delegated_lua='{['+json.dumps(delegated['identity'])+'] = {'+', '.join('['+json.dumps(v)+'] = true' for v in delegated['domains'])+'}}'
entries=[]
for address,owners in source.items():
 entries.append('  ['+json.dumps(address.lower())+'] = {'+', '.join('['+json.dumps(owner.lower())+'] = true' for owner in sorted(set(owners)))+'},')
code='''-- Native authenticated sender ownership; no policy is applied to inbound unauthenticated mail.
local delegated = '''+delegated_lua+'''
local owners = {
'''+ '\n'.join(entries)+'''
}
rspamd_config:register_symbol({
  name = 'BRANDFLEET_SENDER_OWNERSHIP',
  type = 'prefilter',
  priority = 20,
  callback = function(task)
    local user = task:get_user()
    if not user then return false end
    user = string.lower(user)
    local from = task:get_from('mime')
    if not from or #from ~= 1 then
      task:set_pre_result('reject', 'An authenticated sender requires one owned header From address', 'brandfleet_sender_ownership')
      return true
    end
    local address = string.lower(from[1].addr or '')
    local allowed = owners[address] or {}
    local domain = address:match('@([^@]+)$')
    local delegated_allowed = delegated[user] and delegated[user][domain]
    if not allowed[user] and not delegated_allowed then
      task:set_pre_result('reject', 'Authenticated sender does not own header From', 'brandfleet_sender_ownership')
      return true
    end
    return false
  end,
})
'''
p=Path('/etc/rspamd/brandfleet-sender-ownership.lua');p.write_text(code)
local=Path('/etc/rspamd/rspamd.local.lua');old=local.read_text() if local.exists() else ''
line='dofile("/etc/rspamd/brandfleet-sender-ownership.lua")'
if line not in old:local.write_text(old+'\n'+line+'\n')
subprocess.run(['rspamadm','configtest'],check=True,capture_output=True)
if '--no-restart' not in sys.argv: subprocess.run(['systemctl','restart','rspamd'],check=True)
print('Authenticated header From ownership configured for '+str(len(source))+' exact permitted sender addresses.')
