"""Pytest configuration for Module 2 tests."""
import pytest


@pytest.fixture
def sample_email_body():
    return (
        "Dear faculty@srmap.edu.in,\n\n"
        "Please find attached my research paper on Machine Learning applications.\n"
        "The study was conducted over 6 months with significant findings.\n"
        "Feel free to reach out to coordinator@srmap.edu.in for review.\n\n"
        "Best regards,\nDr. Smith"
    )


@pytest.fixture
def pii_email_body():
    return (
        "Student AP2021001234 can be reached at 9876543210 "
        "or via john.doe@gmail.com for further communication. "
        "The research paper covers neural networks and deep learning methodologies. "
        "Please review and provide feedback at your earliest convenience."
    )
