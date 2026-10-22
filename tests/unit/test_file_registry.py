import sys
from pathlib import Path
import os
import tempfile
import base64
import shutil

# Add project root
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.shared.file_registry import FileRegistry

def test_parse_filename():
    registry = FileRegistry()
    
    # Test prefix pattern
    token, display = registry._parse_filename("28a92f58_My_Document_Report.docx")
    assert token == "28a92f58"
    assert display == "My_Document_Report.docx"
    
    # Test suffix pattern
    token, display = registry._parse_filename("my-cool-game_e7da6c0e.html")
    assert token == "e7da6c0e"
    assert display == "my-cool-game.html"
    
    # Test fallback
    token, display = registry._parse_filename("some_other_file_name.txt")
    assert token == "name"
    assert display == "some_other_file.txt"
    
    # Test legacy/no underscore
    token, display = registry._parse_filename("plainfile.csv")
    assert token == "legacy"
    assert display == "plainfile.csv"

def test_get_file_disk_fallback():
    registry = FileRegistry()
    
    # Create a temporary directory structure for this test
    old_permanent_dir = registry.permanent_dir
    old_temp_dir = registry.temp_dir
    
    test_dir = tempfile.mkdtemp()
    registry.permanent_dir = os.path.join(test_dir, "artifacts")
    registry.temp_dir = os.path.join(test_dir, "temp")
    
    os.makedirs(registry.permanent_dir, exist_ok=True)
    os.makedirs(registry.temp_dir, exist_ok=True)
    
    try:
        # Create a physical file in permanent_dir with token suffix
        token = "abcd1234"
        filename = f"test-file_{token}.txt"
        file_path = os.path.join(registry.permanent_dir, filename)
        file_content = b"Hello, unit test world!"
        
        with open(file_path, "wb") as f:
            f.write(file_content)
            
        # Verify get_file returns None for non-existent token
        assert registry.get_file("nonexistent") is None
        
        # Verify get_file successfully falls back to disk and reconstructs metadata
        metadata = registry.get_file(token)
        assert metadata is not None
        assert metadata["token"] == token
        assert metadata["name"] == "test-file.txt"
        assert metadata["size"] == len(file_content)
        assert metadata["mime"] == "text/plain"
        
        # Decode base64 and check content
        decoded = base64.b64decode(metadata["data"])
        assert decoded == file_content
        
        # Verify it cached it in temp_dir
        cached_path = os.path.join(registry.temp_dir, f"{token}.json")
        assert os.path.exists(cached_path)
        
    finally:
        # Restore old directories and clean up
        registry.permanent_dir = old_permanent_dir
        registry.temp_dir = old_temp_dir
        shutil.rmtree(test_dir, ignore_errors=True)
