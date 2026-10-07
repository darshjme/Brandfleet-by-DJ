'use strict';
const http = require('node:http');
const fs = require('node:fs');
const { execFileSync } = require('node:child_process');
const path = '/srv/brandfleet/state.db';
fs.mkdirSync('/srv/brandfleet', {recursive:true});
execFileSync('/usr/bin/sqlite3', [path, 'PRAGMA journal_mode=WAL; CREATE TABLE IF NOT EXISTS hits (id INTEGER PRIMARY KEY, at TEXT NOT NULL);']);
const server = http.createServer((req, res) => {
  if (req.url === '/health') {
    const count = Number(execFileSync('/usr/bin/sqlite3',[path,'SELECT count(*) FROM hits;'],{encoding:'utf8'}).trim());
    res.writeHead(200, {'Content-Type':'application/json'});
    return res.end(JSON.stringify({ok:true,runtime:process.version,db:'SQLite',hits:count}));
  }
  execFileSync('/usr/bin/sqlite3', [path,"INSERT INTO hits(at) VALUES(datetime('now'));"], {timeout:5000});
  res.writeHead(200, {'Content-Type':'text/html; charset=utf-8'});
  res.end('<!doctype html><meta charset="utf-8"><title>Android hosted Node app</title><h1>Node + SQLite inside Android LXC</h1><p>Code and database persist in this Android device’s /data.</p>');
});
server.listen(8081,'0.0.0.0');
process.on('SIGTERM',()=>server.close(()=>{execFileSync('/usr/bin/sqlite3',[path,'PRAGMA wal_checkpoint(TRUNCATE);']);fs.writeFileSync('/srv/brandfleet/graceful-stop.json',JSON.stringify({at:new Date().toISOString(),signal:'SIGTERM',checkpoint:'SQLite WAL truncated'}));process.exit(0)}));
