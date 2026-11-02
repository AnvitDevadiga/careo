CREATE TABLE bookings (
    id INTEGER PRIMARY KEY,
    customer TEXT NOT NULL,
    slot TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active', 'cancelled'))
);
CREATE TABLE events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    booking_id INTEGER NOT NULL,
    kind TEXT NOT NULL
);
