import http.server
import socketserver
import urllib.request
import urllib.error
import urllib.parse
import json
import sys
import sqlite3
import os
from datetime import datetime

PORT = 8081
DB_FILE = 'jira_monitor.db'

def init_db():
    print("\n" + "="*60, flush=True)
    print("   JIRA MONITOR SERVER v20 - NUCLEAR STABILITY EDITION", flush=True)
    print("="*60, flush=True)
    print(f"Current Directory: {os.getcwd()}", flush=True)
    print(f"Script Directory:  {os.path.dirname(os.path.abspath(__file__))}", flush=True)
    
    db_path = os.path.abspath(DB_FILE)
    print(f"Database File:     {db_path}", flush=True)

    # Cleanup Zombie DB if it exists in a higher level (c:\Users\HOPME-PC)
    parent_db = os.path.abspath(os.path.join(os.getcwd(), '..', 'jira_monitor.db'))
    if os.path.exists(parent_db) and "scratch" not in parent_db:
        print(f"!!! WARNING: Found Zombie DB at {parent_db}. PLEASE DELETE IT MANUALLY if issues persist.", flush=True)

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    
    # 1. Check current schema
    c.execute("PRAGMA table_info(issues)")
    rows = c.fetchall()
    columns = [row[1].lower() for row in rows]
    print(f"Database Columns:  {columns}", flush=True)
    
    # 2. NUCLEAR FIX: If 'project' column is missing, recreate table
    if columns and 'project' not in columns:
        print("!!! SCHEMA BREACH DETECTED: Missing 'project' column.", flush=True)
        print("!!! INITIATING NUCLEAR RECONSTRUCTION...", flush=True)
        try:
            c.execute("ALTER TABLE issues RENAME TO issues_old")
            c.execute('''CREATE TABLE issues
                         (key TEXT PRIMARY KEY, 
                          summary TEXT, 
                          assignee TEXT, 
                          project TEXT,
                          status TEXT, 
                          updated TEXT, 
                          resolutiondate TEXT,
                          raw_data TEXT)''')
            
            c.execute("SELECT key, summary, assignee, status, updated, resolutiondate, raw_data FROM issues_old")
            old_rows = c.fetchall()
            print(f"!!! Migrating {len(old_rows)} records to new schema...", flush=True)
            for r in old_rows:
                key = r[0]
                p_key = key.split('-')[0] if '-' in key else 'Unknown'
                c.execute("INSERT INTO issues (key, summary, assignee, project, status, updated, resolutiondate, raw_data) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", 
                         (r[0], r[1], r[2], p_key, r[3], r[4], r[5], r[6]))
            
            c.execute("DROP TABLE issues_old")
            conn.commit()
            print("!!! NUCLEAR RECONSTRUCTION SUCCESSFUL.", flush=True)
        except Exception as e:
            print(f"!!! NUCLEAR ERROR: {e}", flush=True)
            conn.rollback()
    
    # 3. Ensure table exists
    c.execute('''CREATE TABLE IF NOT EXISTS issues
                 (key TEXT PRIMARY KEY, 
                  summary TEXT, 
                   assignee TEXT, 
                  project TEXT,
                  status TEXT, 
                  updated TEXT, 
                  resolutiondate TEXT,
                  raw_data TEXT)''')

    # 4. Populate missing projects
    c.execute("SELECT count(*) FROM issues WHERE project IS NULL OR project = 'Unknown' OR project = ''")
    empty_count = c.fetchone()[0]
    if empty_count > 0:
        print(f"!!! Finalizing migration for {empty_count} items...", flush=True)
        c.execute("SELECT key FROM issues WHERE project IS NULL OR project = 'Unknown' OR project = ''")
        for (key,) in c.fetchall():
            p_key = key.split('-')[0] if '-' in key else 'Unknown'
            c.execute("UPDATE issues SET project = ? WHERE key = ?", (p_key, key))
        conn.commit()

    conn.commit()
    conn.close()
    print("Database is READY and VERIFIED.", flush=True)
    print("="*60 + "\n", flush=True)

class ProxyHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith('/api/proxy'):
            self.handle_proxy()
        elif self.path.startswith('/api/db/issues'):
            self.handle_db_get()
        else:
            super().do_GET()

    def do_POST(self):
        if self.path.startswith('/api/proxy'):
            self.handle_proxy()
        elif self.path.startswith('/api/db/sync'):
            self.handle_db_sync()
        else:
            self.send_error(404, "Not Found")
    
    def do_OPTIONS(self):
        self.send_response(200, "OK")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type, Accept")
        self.end_headers()

    def handle_db_get(self):
        try:
            parsed = urllib.parse.urlparse(self.path)
            q = urllib.parse.parse_qs(parsed.query)
            days = int(q.get('days', ['30'])[0])
            
            # Calculate cutoff date
            # Jira dates are ISO 8601, e.g., 2023-10-27T10:00:00.000+0000
            # We can use string comparison if we format correctly, 
            # but simplest is just rudimentary string comparison for "YYYY-MM-DD"
            # or rely on SQLite's datetime if formats align.
            # Let's use Python to get the cutoff timestamp string.
            from datetime import timedelta
            cutoff = (datetime.now() - timedelta(days=days)).isoformat()
            
            # We compare string to string. 
            # Note: Jira dates might have timezone offsets. 
            # For simplicity in this local tool, simple lexicographical comparison 
            # of ISO strings is usually "good enough" for "30 days".
            
            query = "SELECT raw_data FROM issues WHERE updated >= ? OR (resolutiondate IS NOT NULL AND resolutiondate != '' AND resolutiondate >= ?) ORDER BY updated DESC"
            
            conn = sqlite3.connect(DB_FILE)
            c = conn.cursor()
            c.execute(query, (cutoff, cutoff))
            # We fetch all, but we could fetch one by one to save memory if needed. 
            # For < 10k issues, fetchall is fine and faster than many roundtrips.
            rows = c.fetchall()
            conn.close()
            
            # Send JSON Header
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            
            # Stream the response: {"issues": [row1, row2, ...]}
            self.wfile.write(b'{"issues": [')
            
            # Efficiently join and write
            # Avoid list comprehension which creates a huge list in memory
            for i, row in enumerate(rows):
                if i > 0:
                    self.wfile.write(b',')
                # row[0] is string, encode to bytes
                self.wfile.write(row[0].encode('utf-8'))
                
            self.wfile.write(b']}')
            
        except Exception as e:
            print(f"DB Get Error: {e}")
            # If headers not sent, send 500. If sent, we are kind of broken.
            try: self.send_error(500, str(e))
            except: pass

    def handle_db_sync(self):
        try:
            content_len = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_len)
            data = json.loads(body)
            issues = data.get('issues', [])
            
            print(f"DB Sync Start: Received {len(issues)} issues from client")
            
            conn = sqlite3.connect(DB_FILE)
            c = conn.cursor()
            
            count = 0
            for issue in issues:
                key = issue.get('key')
                fields = issue.get('fields', {})
                summary = fields.get('summary', '')
                assignee = fields.get('assignee', {}).get('displayName', 'Unassigned') if fields.get('assignee') else 'Unassigned'
                status = fields.get('status', {}).get('name', 'Unknown')
                updated = fields.get('updated', '')
                resolutiondate = fields.get('resolutiondate', '')
                
                project_key = fields.get('project', {}).get('key', 'Unknown')
                
                c.execute('''INSERT OR REPLACE INTO issues 
                             (key, summary, assignee, project, status, updated, resolutiondate, raw_data) 
                             VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                          (key, summary, assignee, project_key, status, updated, resolutiondate, json.dumps(issue)))
                count += 1
                
            conn.commit()
            conn.close()
            
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "synced", "count": count}).encode('utf-8'))
            print(f"DB Sync Complete: {count} issues inserted/updated")
            
        except Exception as e:
            print(f"DB Sync Error: {e}")
            self.send_error(500, str(e))

    def handle_proxy(self):
        parsed_path = urllib.parse.urlparse(self.path)
        query_params = urllib.parse.parse_qs(parsed_path.query)
        target_url = query_params.get('url', [None])[0]

        if not target_url:
            self.send_error(400, "Missing 'url' query parameter")
            return

        print(f"Proxying to: {target_url}", flush=True)

        try:
            allowed_headers = ['authorization', 'content-type', 'accept', 'x-atlassian-token']
            headers = {}
            for key, value in self.headers.items():
                if key.lower() in allowed_headers:
                    headers[key] = value

            data = None
            if self.command == 'POST':
                content_len = int(self.headers.get('Content-Length', 0))
                if content_len > 0:
                    data = self.rfile.read(content_len)

            req = urllib.request.Request(target_url, data=data, headers=headers, method=self.command)

            with urllib.request.urlopen(req, timeout=30) as response:
                content = response.read()
                self.send_response(response.status)
                self.send_header("Content-Type", response.getheader("Content-Type", "application/json"))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(content)

        except urllib.error.HTTPError as e:
            err_body = e.read()
            print(f"!!! Upstream Error {e.code}: {err_body}", flush=True)
            try:
                self.send_response(e.code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(err_body)
            except: pass
        except Exception as e:
            print(f"!!! Proxy Error: {e}", flush=True)
            try: self.send_error(500, str(e)) 
            except: pass

print(f"Starting server at http://localhost:{PORT}")

# Allow address reuse
class ReusableThreadingTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True

if __name__ == '__main__':
    # Change working directory to script location FIRST
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    
    # Print with flush=True for real-time logs
    print("Initializing server and database...", flush=True)
    init_db()
    
    try:
        with ReusableThreadingTCPServer(("", PORT), ProxyHTTPRequestHandler) as httpd:
            print(f"Server is now running at http://localhost:{PORT}", flush=True)
            httpd.serve_forever()
    except OSError as e:
        if e.errno == 98 or e.errno == 10048:
            print(f"ERROR: Port {PORT} is already in use!", flush=True)
    except KeyboardInterrupt:
        print("\nStopping...", flush=True)
