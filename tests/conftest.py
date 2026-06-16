"""
Pytest configuration and shared fixtures.
"""
import asyncio
import pytest
from typing import AsyncGenerator

# Use asyncio event loop for all async tests
@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()
