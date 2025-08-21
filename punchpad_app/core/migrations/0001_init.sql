-- 0001: initial schema

-- employees
CREATE TABLE IF NOT EXISTS employees (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  pin_hash TEXT NOT NULL,
  pay_rate REAL NOT NULL DEFAULT 0,
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_employees_active ON employees(active);

-- punches
CREATE TABLE IF NOT EXISTS punches (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
  clock_in  TEXT NOT NULL,
  clock_out TEXT,
  method    TEXT NOT NULL CHECK (method IN ('kiosk','manual')),
  note      TEXT,
  CHECK (clock_out IS NULL OR clock_out >= clock_in)
);
CREATE INDEX IF NOT EXISTS idx_punches_emp_clockin ON punches(employee_id, clock_in);

-- absences
CREATE TABLE IF NOT EXISTS absences (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
  day   TEXT NOT NULL,
  reason TEXT,
  UNIQUE(employee_id, day)
);

-- settings
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

-- audit_log
CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  actor TEXT NOT NULL,
  action TEXT NOT NULL,
  target_type TEXT NOT NULL,
  target_id INTEGER,
  meta_json TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_created_at ON audit_log(created_at);

-- schema_migrations
CREATE TABLE IF NOT EXISTS schema_migrations(
  version INTEGER PRIMARY KEY
);
