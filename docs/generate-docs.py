#!/usr/bin/env python3
"""
Generic script to generate documentation SVGs from command output
Generates SVG directly from ANSI codes without using Rich to avoid truncation issues
Usage: python3 generate-docs.py
"""

import subprocess
import os
import re
from pathlib import Path
from html import escape

# Configuration
SCRIPT_DIR = Path(__file__).parent.parent  # Parent directory (project root)
DOCS_DIR = Path(__file__).parent  # Current directory (docs/)
DOCS_DIR.mkdir(exist_ok=True)

# Fixed width for terminal capture (characters)
TERMINAL_WIDTH = 100

# ANSI color codes mapping to hex colors
ANSI_COLORS = {
    '30': '#000000',  # Black
    '31': '#ff2627',  # Red
    '32': '#00823d',  # Green
    '33': '#d08442',  # Yellow
    '34': '#68a0b3',  # Blue
    '35': '#ff2c7a',  # Magenta
    '36': '#398280',  # Cyan
    '37': '#c5c8c6',  # White
    '90': '#868887',  # Bright Black (Gray)
    '91': '#ff2627',  # Bright Red
    '92': '#00823d',  # Bright Green
    '93': '#d0b344',  # Bright Yellow
    '94': '#68a0b3',  # Bright Blue
    '95': '#ff2c7a',  # Bright Magenta
    '96': '#398280',  # Bright Cyan
    '97': '#c5c8c6',  # Bright White
}

# Default colors
DEFAULT_FG = '#c5c8c6'
DEFAULT_BG = '#292929'

def run_command(cmd, env=None):
    """Execute a command and return its output with ANSI codes"""
    env = env or os.environ.copy()
    env['COLUMNS'] = str(TERMINAL_WIDTH)
    env['NO_HYPERLINKS'] = '1'  # Disable hyperlinks to avoid OSC sequences in SVG
    result = subprocess.run(
        cmd,
        shell=True,
        capture_output=True,
        text=True,
        env=env,
        cwd=SCRIPT_DIR
    )
    return result.stdout + result.stderr

def clean_osc_sequences(text):
    """Remove only OSC (Operating System Command) sequences that cause XML issues"""
    # Remove OSC hyperlink sequences like ]8;;url]8;; or ]8;;url\x07
    text = re.sub(r'\x1B\]8;;[^\x1B]*\x1B\\', '', text)
    text = re.sub(r'\x1B\]8;;[^\x07]*\x07', '', text)  # Alternative OSC terminator
    # Remove other control characters that are invalid in XML (except newline, tab, carriage return)
    text = re.sub(r'[\x00-\x08\x0B-\x0C\x0E-\x1A\x1C-\x1F\x7F]', '', text)
    return text

def clean_text_for_xml(text):
    """Clean text to be safe for XML (remove only problematic characters, keep ANSI codes)"""
    # Only remove control characters that are invalid in XML
    # Keep ANSI codes as they will be parsed separately
    text = re.sub(r'[\x00-\x08\x0B-\x0C\x0E-\x1A\x1C-\x1F\x7F]', '', text)
    return text

def parse_ansi_line(line):
    """Parse an ANSI line and return list of (text, color, bold) tuples"""
    parts = []
    current_color = DEFAULT_FG
    current_bold = False
    
    # Find all ANSI codes and their positions
    # Match CSI sequences: ESC[ followed by parameters and command
    ansi_pattern = re.compile(r'\x1B\[([0-9;]*)([a-zA-Z])')
    matches = list(ansi_pattern.finditer(line))
    
    # Build segments: text between ANSI codes
    last_pos = 0
    for match in matches:
        # Text before this ANSI code
        if match.start() > last_pos:
            text = line[last_pos:match.start()]
            # Clean only problematic control chars, keep text as-is (including spaces)
            text = clean_text_for_xml(text)
            # Always add text segment, even if it's just spaces (they're important!)
            if text is not None:
                parts.append((text, current_color, current_bold))
        
        # Process ANSI code
        code = match.group(1)
        command = match.group(2)
        
        if command == 'm':  # SGR (Select Graphic Rendition)
            codes = [c for c in code.split(';') if c]
            for c in codes:
                if c == '0' or c == '00':
                    current_color = DEFAULT_FG
                    current_bold = False
                elif c == '1' or c == '01':
                    current_bold = True
                elif c == '22' or c == '21':  # Normal intensity
                    current_bold = False
                elif c in ANSI_COLORS:
                    current_color = ANSI_COLORS[c]
                elif len(c) == 2:
                    if c.startswith('3'):  # Foreground color (30-37)
                        if c in ANSI_COLORS:
                            current_color = ANSI_COLORS[c]
                    elif c.startswith('9'):  # Bright foreground (90-97)
                        if c in ANSI_COLORS:
                            current_color = ANSI_COLORS[c]
        
        last_pos = match.end()
    
    # Add remaining text
    if last_pos < len(line):
        text = line[last_pos:]
        # Clean only problematic control chars, keep text as-is (including spaces)
        text = clean_text_for_xml(text)
        # Always add text segment, even if it's just spaces (they're important!)
        if text is not None:
            parts.append((text, current_color, current_bold))
    
    # If no parts (line was all ANSI codes), return empty
    if not parts:
        return []
    
    return parts

def generate_svg(cmd, output_file, title):
    """Generate an SVG directly from ANSI command output"""
    print(f"Generating {output_file}...")
    
    # Execute command and capture output
    output = run_command(cmd)
    output = output.replace('\r', '')
    
    # Clean OSC sequences first (hyperlinks) that cause XML issues
    output = clean_osc_sequences(output)
    
    # Parse lines
    lines = output.split('\n')
    
    # Calculate dimensions
    max_line_len = max(len(re.sub(r'\x1B\[[0-9;]*[a-zA-Z]', '', line)) for line in lines) if lines else 0
    
    # Count lines properly (including empty lines for spacing)
    non_empty_lines = len([l for l in lines if l.strip()])
    empty_lines = len([l for l in lines if not l.strip()])
    # Empty lines take less space but still need some
    num_lines = non_empty_lines + empty_lines * 0.3
    
    # SVG dimensions
    char_width = 12.2
    line_height = 24.4
    padding = 20
    title_height = 40
    svg_width = max(TERMINAL_WIDTH * char_width, max_line_len * char_width) + padding * 2
    # Calculate height with proper margin to prevent clipping
    # Add extra margin (150px) to ensure nothing gets cut off
    svg_height = int(num_lines * line_height + title_height + padding * 2 + 150)
    
    # Generate SVG
    svg_parts = []
    svg_parts.append(f'''<svg class="terminal-output" viewBox="0 0 {svg_width:.1f} {svg_height:.1f}" xmlns="http://www.w3.org/2000/svg">
    <style>
        .terminal-text {{
            font-family: "Fira Code", monospace;
            font-size: 20px;
            line-height: {line_height}px;
        }}
        .terminal-title {{
            font-size: 18px;
            font-weight: bold;
            font-family: arial;
        }}
    </style>
    
    <defs>
        <clipPath id="terminal-clip">
            <rect x="0" y="0" width="{svg_width - padding * 2:.1f}" height="{svg_height - title_height - padding:.1f}" />
        </clipPath>
    </defs>
    
    <!-- Background -->
    <rect x="0" y="0" width="{svg_width:.1f}" height="{svg_height:.1f}" fill="{DEFAULT_BG}" stroke="#555" stroke-width="1"/>
    
    <!-- Title -->
    <text class="terminal-title" x="{svg_width // 2:.1f}" y="{title_height - 10}" text-anchor="middle" fill="{DEFAULT_FG}">{escape(title)}</text>
    
    <!-- Terminal content -->
    <g transform="translate({padding}, {title_height})" clip-path="url(#terminal-clip)">
''')
    
    y_pos = line_height
    for line in lines:
        if not line.strip() and y_pos == line_height:
            continue  # Skip leading empty lines
        
        parts = parse_ansi_line(line)
        x_pos = 0
        
        for text, color, bold in parts:
            # Skip completely empty text segments (but preserve spaces!)
            if text is None:
                continue
            
            # Clean text for XML safety (should already be clean, but double-check)
            # Always preserve text, even if it's just spaces
            text_clean = clean_text_for_xml(text)
            if text_clean is None:
                continue
            
            # Escape HTML entities (this preserves spaces)
            text_escaped = escape(text_clean)
            font_weight = "bold" if bold else "normal"
            
            # Use xml:space="preserve" to ensure spaces are rendered correctly
            svg_parts.append(f'        <text class="terminal-text" x="{x_pos:.1f}" y="{y_pos:.1f}" fill="{color}" font-weight="{font_weight}" xml:space="preserve">{text_escaped}</text>')
            x_pos += len(text_clean) * char_width
        
        # Move to next line
        y_pos += line_height
        # Add extra space for empty lines
        if not line.strip():
            y_pos += line_height * 0.3
    
    svg_parts.append('    </g>')
    svg_parts.append('</svg>')
    
    # Write SVG
    svg_path = DOCS_DIR / output_file
    with open(svg_path, 'w') as f:
        f.write('\n'.join(svg_parts))
    
    print(f"  ✓ Generated {output_file} (width: {svg_width:.1f}px, height: {svg_height:.1f}px)")

def main():
    """Main entry point"""
    print("Generating documentation SVGs...\n")
    
    # Generate subinfo
    generate_svg(
        "python3 subinfo.py QmasYjypV6nTLp4iNH4Vjf7fksRNxAkAskqDdKf2DCsQkV",
        "subinfo-example.svg",
        "subinfo"
    )
    
    # Generate indexerinfo
    generate_svg(
        "python3 indexerinfo.py ellipfra",
        "indexerinfo-example.svg",
        "indexerinfo"
    )
    
    print("\n✅ Documentation generated successfully!")
    print(f"   - {DOCS_DIR / 'subinfo-example.svg'}")
    print(f"   - {DOCS_DIR / 'indexerinfo-example.svg'}")

if __name__ == "__main__":
    main()
