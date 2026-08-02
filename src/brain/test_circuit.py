import unittest, tempfile, os
from brain import circuit

class TestCircuit(unittest.TestCase):
    def test_under_limit(self):
        c = circuit.CircuitBreaker(limit=1000)
        self.assertFalse(c.record(600))
        self.assertFalse(c.is_tripped())

    def test_over_limit_trips(self):
        c = circuit.CircuitBreaker(limit=1000)
        self.assertFalse(c.record(600))
        self.assertTrue(c.record(600))
        self.assertTrue(c.is_tripped())

    def test_summary(self):
        c = circuit.CircuitBreaker(limit=1000)
        c.record(600)
        self.assertIn("600", c.summary())

if __name__ == "__main__":
    unittest.main()
