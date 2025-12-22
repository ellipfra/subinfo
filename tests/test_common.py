#!/usr/bin/env python3
"""
Unit tests for common.py module
"""

import pytest
import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common import (
    Colors, terminal_link, format_deployment_link,
    format_tokens, format_tokens_short, format_percentage,
    format_timestamp, format_duration, strip_ansi, get_display_width
)


class TestFormatTokens:
    """Tests for token formatting functions"""
    
    def test_format_tokens_zero(self):
        assert format_tokens('0') == '0 GRT'
    
    def test_format_tokens_small(self):
        result = format_tokens('1000000000000000000')  # 1 GRT in wei
        assert '1' in result
        assert 'GRT' in result
    
    def test_format_tokens_large(self):
        result = format_tokens('1000000000000000000000000')  # 1M GRT in wei
        assert 'GRT' in result
        assert '1' in result
    
    def test_format_tokens_with_decimals(self):
        result = format_tokens('1500000000000000000')  # 1.5 GRT in wei
        assert 'GRT' in result
    
    def test_format_tokens_short_zero(self):
        result = format_tokens_short('0')
        assert result in ['0', '0.0', '0 GRT', '0.0 GRT', '0.00']
    
    def test_format_tokens_short_small(self):
        result = format_tokens_short('1000000000000000000')  # 1 GRT
        assert '1' in result
    
    def test_format_tokens_short_millions(self):
        result = format_tokens_short('1000000000000000000000000')  # 1M GRT
        assert 'M' in result or '1000000' in result or '1,000,000' in result


class TestFormatPercentage:
    """Tests for percentage formatting (input is PPM - parts per million)"""
    
    def test_format_percentage_zero(self):
        result = format_percentage(0)
        assert '0' in result
        assert '%' in result
    
    def test_format_percentage_hundred(self):
        # 100% = 1,000,000 PPM
        result = format_percentage(1000000)
        assert '100' in result
        assert '%' in result
    
    def test_format_percentage_ten_percent(self):
        # 10% = 100,000 PPM
        result = format_percentage(100000)
        assert '10' in result
        assert '%' in result


class TestFormatTimestamp:
    """Tests for timestamp formatting"""
    
    def test_format_timestamp_zero(self):
        result = format_timestamp('0')
        # Unix epoch is 1970-01-01 00:00:00 UTC, but depends on local timezone
        assert result is not None and len(result) > 0
    
    def test_format_timestamp_recent(self):
        import time
        now = str(int(time.time()))
        result = format_timestamp(now)
        # Should contain current year or time components
        assert len(result) > 0
    
    def test_format_timestamp_invalid(self):
        # Should handle invalid input gracefully
        result = format_timestamp('invalid')
        # Either returns something sensible or indicates error
        assert result is not None


class TestFormatDuration:
    """Tests for duration formatting"""
    
    def test_format_duration_seconds(self):
        result = format_duration(30)
        assert 's' in result or 'sec' in result or '30' in result
    
    def test_format_duration_minutes(self):
        result = format_duration(120)
        assert 'm' in result or 'min' in result or '2' in result
    
    def test_format_duration_hours(self):
        result = format_duration(3600)
        assert 'h' in result or 'hour' in result or '1' in result
    
    def test_format_duration_days(self):
        result = format_duration(86400)
        assert 'd' in result or 'day' in result or '1' in result
    
    def test_format_duration_zero(self):
        result = format_duration(0)
        assert result is not None


class TestStripAnsi:
    """Tests for ANSI code stripping"""
    
    def test_strip_ansi_no_codes(self):
        text = "Hello World"
        assert strip_ansi(text) == "Hello World"
    
    def test_strip_ansi_with_color(self):
        text = f"{Colors.BRIGHT_GREEN}Hello{Colors.RESET}"
        result = strip_ansi(text)
        assert 'Hello' in result
        assert '\033' not in result
    
    def test_strip_ansi_multiple_codes(self):
        text = f"{Colors.BOLD}{Colors.RED}Bold Red{Colors.RESET}"
        result = strip_ansi(text)
        assert 'Bold Red' in result
        assert '\033' not in result


class TestGetDisplayWidth:
    """Tests for display width calculation"""
    
    def test_display_width_plain(self):
        text = "Hello"
        assert get_display_width(text) == 5
    
    def test_display_width_with_ansi(self):
        text = f"{Colors.BRIGHT_GREEN}Hello{Colors.RESET}"
        assert get_display_width(text) == 5
    
    def test_display_width_empty(self):
        assert get_display_width("") == 0


class TestTerminalLink:
    """Tests for terminal link generation"""
    
    def test_terminal_link_basic(self):
        url = "https://example.com"
        text = "Example"
        result = terminal_link(url, text)
        # Should contain the text
        assert "Example" in result
    
    def test_terminal_link_empty_text(self):
        url = "https://example.com"
        text = ""
        result = terminal_link(url, text)
        # Should still produce some output
        assert result is not None


class TestFormatDeploymentLink:
    """Tests for deployment link formatting"""
    
    def test_format_deployment_link_hash_only(self):
        ipfs_hash = "QmXYZ123456789abcdef"
        result = format_deployment_link(ipfs_hash)
        # Should contain the hash or part of it
        assert "QmXYZ" in result or "123" in result
    
    def test_format_deployment_link_with_subgraph_id(self):
        ipfs_hash = "QmXYZ123456789abcdef"
        subgraph_id = "0x1234567890abcdef"
        result = format_deployment_link(ipfs_hash, subgraph_id)
        # Should contain the hash
        assert "QmXYZ" in result or "123" in result


class TestColors:
    """Tests for Colors class"""
    
    def test_colors_exist(self):
        # Ensure all expected color attributes exist
        assert hasattr(Colors, 'RESET')
        assert hasattr(Colors, 'BOLD')
        assert hasattr(Colors, 'DIM')
        assert hasattr(Colors, 'RED')
        assert hasattr(Colors, 'GREEN')
        assert hasattr(Colors, 'YELLOW')
        assert hasattr(Colors, 'BLUE')
        assert hasattr(Colors, 'CYAN')
    
    def test_colors_are_escape_sequences(self):
        # ANSI escape sequences start with \033[ or \x1b[
        assert Colors.RESET.startswith('\033') or Colors.RESET.startswith('\x1b')
        assert Colors.BOLD.startswith('\033') or Colors.BOLD.startswith('\x1b')


if __name__ == '__main__':
    pytest.main([__file__, '-v'])

