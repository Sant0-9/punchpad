-- 0002: kiosk pin attempts and indexes

CREATE TABLE IF NOT EXISTS pin_attempts (
  id INTEGER PRIMARY KEY,
  ts TEXT NOT NULL,
  source TEXT NOT NULL,
  success INTEGER NOT NULL,
  employee_id INTEGER NULL,
  reason TEXT NULL
);

CREATE INDEX IF NOT EXISTS idx_pin_attempts_source_ts ON pin_attempts(source, ts);
CREATE INDEX IF NOT EXISTS idx_pin_attempts_emp_ts ON pin_attempts(employee_id, ts);

-- Seed default kiosk settings if missing
INSERT OR IGNORE INTO settings(key, value) VALUES
 ('kiosk.debounce_seconds', '30'),
 ('kiosk.pin_attempt_window_seconds', '300'),
 ('kiosk.pin_max_attempts_per_window', '5'),
 ('kiosk.lockout_minutes', '10');
