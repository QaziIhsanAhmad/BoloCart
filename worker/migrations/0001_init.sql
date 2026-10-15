CREATE TABLE IF NOT EXISTS orders(
  order_gid TEXT PRIMARY KEY, order_name TEXT, phone TEXT, first_name TEXT,
  total TEXT, status TEXT, created_at INTEGER, updated_at INTEGER, wamid TEXT);
CREATE TABLE IF NOT EXISTS messages(
  id INTEGER PRIMARY KEY AUTOINCREMENT, direction TEXT, phone TEXT, wamid TEXT,
  kind TEXT, body TEXT, result TEXT, ts INTEGER);
CREATE UNIQUE INDEX IF NOT EXISTS ux_in_wamid ON messages(wamid) WHERE direction='in';
CREATE TABLE IF NOT EXISTS windows(phone TEXT PRIMARY KEY, last_inbound INTEGER);
CREATE TABLE IF NOT EXISTS costs(
  wamid TEXT PRIMARY KEY, category TEXT, billable INTEGER, pricing_type TEXT, ts INTEGER);
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY AUTOINCREMENT, level TEXT, text TEXT, ts INTEGER);
