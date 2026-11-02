INSERT INTO bookings (id, customer, slot, status) VALUES (1, 'existing', '08:00', 'active');
INSERT INTO events (booking_id, kind) VALUES (1, 'created');
