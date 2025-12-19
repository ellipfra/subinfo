#!/usr/bin/env python3
"""
Simple script to generate documentation SVGs
Usage: python3 generate-docs.py
"""

import subprocess
import sys
import os
from pathlib import Path
from rich.console import Console
from rich.text import Text
import io
import re

# Configuration
SCRIPT_DIR = Path(__file__).parent.parent  # Parent directory (project root)
DOCS_DIR = Path(__file__).parent  # Current directory (docs/)
DOCS_DIR.mkdir(exist_ok=True)

def run_command(cmd, env=None):
    """Execute a command and return its output with ANSI codes"""
    env = env or os.environ.copy()
    env['COLUMNS'] = '200'  # Wide width to avoid wrapping
    result = subprocess.run(
        cmd,
        shell=True,
        capture_output=True,
        text=True,
        env=env,
        cwd=SCRIPT_DIR
    )
    return result.stdout + result.stderr

def generate_svg(cmd, output_file, title, target_width=None):
    """Generate an SVG directly from a command"""
    print(f"Generating {output_file}...")
    
    # Execute command and capture output
    output = run_command(cmd)
    
    # Clean output
    output = output.replace('\r', '')
    
    # Calculate required width
    ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    clean_output = ansi_escape.sub('', output)
    max_line_len = max(len(line) for line in clean_output.split('\n')) if clean_output else 0
    width = max(120, max_line_len + 20)
    
    # Create Rich console
    console = Console(
        width=width,
        record=True,
        force_terminal=True,
        file=io.StringIO()
    )
    
    # Display each line with ANSI colors
    for line in output.split('\n'):
        if line.strip():
            text = Text.from_ansi(line)
            console.print(text, end='\n', overflow='crop', no_wrap=True)
        else:
            console.print('', end='\n')
    
    # Save SVG
    svg_path = DOCS_DIR / output_file
    console.save_svg(str(svg_path), title=title)
    
    # Read and fix SVG
    with open(svg_path, 'r') as f:
        svg_content = f.read()
    
    # Determine target width
    if target_width:
        needed_width = target_width
    elif 'subinfo' in output_file:
        needed_width = 1300
    else:
        needed_width = min(1500, max(1300, int(max_line_len * 12.2 + 20)))
    
    # Fix widths
    svg_content = re.sub(
        r'(<rect x="0" y="0" width=")(\d+\.?\d*)(" height="[^"]*" />)',
        lambda m: m.group(1) + str(needed_width) + m.group(3),
        svg_content
    )
    svg_content = re.sub(
        r'(<rect fill="#292929"[^>]*width=")(\d+\.?\d*)(")',
        lambda m: m.group(1) + str(needed_width) + m.group(3),
        svg_content
    )
    svg_content = re.sub(
        r'viewBox="0 0 \d+',
        f'viewBox="0 0 {needed_width + 19}',
        svg_content
    )
    svg_content = re.sub(r'stroke-width="\d+"', 'stroke-width="1"', svg_content)
    
    # Center title
    svg_content = re.sub(
        r'(<text class="terminal-[^"]*-title"[^>]*text-anchor="middle" x=")\d+(")',
        lambda m: m.group(1) + str(needed_width // 2) + m.group(2),
        svg_content
    )
    
    # Update clipPath widths
    svg_content = re.sub(
        r'(<rect x="0" y="[^"]*" width=")(\d+\.?\d*)(" height="[^"]*"/>)',
        lambda m: m.group(1) + str(needed_width) + m.group(3) if float(m.group(2)) < needed_width else m.group(0),
        svg_content
    )
    
    # For subinfo: fix truncated "Acti" text
    if 'subinfo' in output_file:
        # Build indexer -> full "Active (...)" text map
        indexer_to_active = {}
        for line in clean_output.split('\n'):
            if 'Active' in line and '(' in line:
                indexer_match = re.search(
                    r'(suntzu-indexer-arb\.\.\.|differential-graph\.\.\.|braindexer\.eth|p-ops2\.eth|[a-z0-9-]+\.eth)',
                    line
                )
                if indexer_match:
                    indexer = indexer_match.group(1)
                    active_match = re.search(r'Active\s+\([^)]+\)', line)
                    if active_match:
                        indexer_to_active[indexer] = active_match.group(0)
        
        # Replace "Acti</text>" with full text
        acti_pattern = r'Acti</text>'
        matches = list(re.finditer(acti_pattern, svg_content))
        
        for match in reversed(matches):
            pos = match.start()
            before = svg_content[max(0, pos-800):pos]
            
            indexer_found = None
            for indexer_pattern in [
                r'(suntzu-indexer-arb\.\.\.)',
                r'(differential-graph\.\.\.)',
                r'(braindexer\.eth)',
                r'(p-ops2\.eth)',
                r'([a-z0-9-]+\.eth)'
            ]:
                indexer_match = re.search(indexer_pattern, before)
                if indexer_match:
                    indexer_found = indexer_match.group(1)
                    break
            
            if indexer_found and indexer_found in indexer_to_active:
                full_text = indexer_to_active[indexer_found]
                svg_content = svg_content[:match.start()] + full_text + svg_content[match.end():]
                
                # Update textLength
                text_elem_start = svg_content.rfind('<text', max(0, pos-200), pos)
                if text_elem_start != -1:
                    text_elem_end = svg_content.find('</text>', pos) + 7
                    if text_elem_end > pos:
                        text_elem = svg_content[text_elem_start:text_elem_end]
                        new_length = len(full_text) * 12.2
                        text_elem_new = re.sub(
                            r'textLength="[^"]*"',
                            f'textLength="{new_length:.1f}"',
                            text_elem
                        )
                        svg_content = svg_content[:text_elem_start] + text_elem_new + svg_content[text_elem_end:]
        
        # Fix malformed XML
        svg_content = re.sub(r'(Active\s+\([^)]+\))<text', r'\1</text><text', svg_content)
        svg_content = re.sub(
            r'(Active \([^)]+\)</text>)(<text[^>]*x="976"[^>]*textLength="[^"]*"[^>]*>\s*</text>)',
            r'\1',
            svg_content
        )
    
    # Save corrected SVG
    with open(svg_path, 'w') as f:
        f.write(svg_content)
    
    print(f"  ✓ Generated {output_file} (width: {needed_width}px)")

def main():
    """Main entry point"""
    print("Generating documentation SVGs...\n")
    
    # Generate subinfo
    generate_svg(
        "python3 subinfo.py QmasYjypV6nTLp4iNH4Vjf7fksRNxAkAskqDdKf2DCsQkV",
        "subinfo-example.svg",
        "subinfo",
        target_width=1300
    )
    
    # Generate indexerinfo
    generate_svg(
        "python3 indexerinfo.py ellipfra",
        "indexerinfo-example.svg",
        "indexerinfo",
        target_width=1500
    )
    
    print("\n✅ Documentation generated successfully!")
    print(f"   - {DOCS_DIR / 'subinfo-example.svg'}")
    print(f"   - {DOCS_DIR / 'indexerinfo-example.svg'}")

if __name__ == "__main__":
    main()
