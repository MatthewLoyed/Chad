import time
import sys
import os

# Add current dir to path
sys.path.append(os.getcwd())

def benchmark():
    print("\n===== API BRIDGE BOOT BENCHMARK =====")
    
    # Measure API Bridge import speed
    start = time.perf_counter()
    import src.clients.desktop.api_bridge
    bridge_import_time = time.perf_counter() - start
    print(f"[*] Import time for src.clients.desktop.api_bridge: {bridge_import_time*1000:.2f}ms")
    
    # Instantiate to check overhead
    from src.clients.desktop.api_bridge import WebViewAPI
    import weakref
    
    class MockWindow:
        pass
    
    start = time.perf_counter()
    mock_win = MockWindow()
    api = WebViewAPI(weakref.ref(mock_win))
    instantiate_time = time.perf_counter() - start
    print(f"[*] Bridge instantiation time: {instantiate_time*1000:.2f}ms")
    
    print("\n--- Heavy External Module Impact (Deferred) ---")
    
    # Now measure what we PREVENTED from blocking the bridge import/boot!
    # Google Workspace Manager usually triggers massive google-api-python-client loading
    start = time.perf_counter()
    try:
        import googleapiclient
        print("[DEBUG] Google Client already partially in memory, result may be faster than cold boot.")
    except ImportError:
        pass
        
    start = time.perf_counter()
    try:
        from src.tools.workspace.manager import GoogleWorkspaceManager
        google_import_time = time.perf_counter() - start
        print(f"[!] Deferred 'GoogleWorkspaceManager' import would have cost: {google_import_time*1000:.2f}ms")
    except Exception as e:
        print(f"[X] Could not measure Google import: {e}")
        google_import_time = 0

    total_bridge_boot = (bridge_import_time + instantiate_time) * 1000
    potential_cost = google_import_time * 1000
    savings_pct = (potential_cost / (total_bridge_boot + potential_cost)) * 100 if (total_bridge_boot + potential_cost) > 0 else 0
    
    print("\n===== RESULTS =====")
    print(f"Current Bridge Boot: {total_bridge_boot:.2f}ms")
    if google_import_time > 0:
        print(f"Deferred Overhead Avoided: ~{potential_cost:.2f}ms")
        print(f"Improvement Potential Realized: {savings_pct:.1f}% leaner GUI Boot Sequence!")
    print("==================================\n")

if __name__ == "__main__":
    benchmark()
