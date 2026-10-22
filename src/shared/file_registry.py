import os
import json
import uuid
import tempfile
import logging

logger = logging.getLogger("FileRegistry")

class FileRegistry:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(FileRegistry, cls).__new__(cls)
            # Permanent artifacts in project root
            from src.shared.paths import get_project_root
            cls._instance.permanent_dir = os.path.join(get_project_root(), "artifacts")
            # Temporary metadata for session tracking
            cls._instance.temp_dir = os.path.join(tempfile.gettempdir(), "chad_artifacts")
            
            os.makedirs(cls._instance.permanent_dir, exist_ok=True)
            os.makedirs(cls._instance.temp_dir, exist_ok=True)
        return cls._instance

    def _parse_filename(self, filename: str):
        """Parse a filename from the permanent artifacts dir into (token, display_name)."""
        filename_base, ext = os.path.splitext(filename)
        if '_' not in filename_base:
            return "legacy", filename
            
        parts = filename_base.split('_')
        is_hex = lambda s: len(s) == 8 and all(c in '0123456789abcdefABCDEF' for c in s)
        
        # Check first part (prefix)
        if is_hex(parts[0]):
            return parts[0], "_".join(parts[1:]) + ext
            
        # Check last part (suffix)
        if is_hex(parts[-1]):
            return parts[-1], "_".join(parts[:-1]) + ext
            
        # Fallback to rsplit
        try:
            display_base, token = filename_base.rsplit('_', 1)
            return token, f"{display_base}{ext}"
        except Exception:
            return "legacy", filename

    def store_file(self, name, data_base64, mime, session_id=None):
        token = uuid.uuid4().hex[:8]
        
        # 1. Save binary file to permanent artifacts folder
        permanent_path = None
        size = 0
        try:
            import base64
            # If it's already base64 string, decode to bytes
            if isinstance(data_base64, str):
                file_bytes = base64.b64decode(data_base64)
            else:
                file_bytes = data_base64
            
            size = len(file_bytes)

            # Unique filename to prevent collisions: [original_name]_[token]
            base, ext = os.path.splitext(name)
            storage_name = f"{base}_{token}{ext}"
            permanent_path = os.path.join(self.permanent_dir, storage_name)
            
            with open(permanent_path, "wb") as f:
                f.write(file_bytes)
            
            logger.info(f"[ REGISTRY ] : Physical file saved to {permanent_path}")
        except Exception as e:
            logger.error(f"[ REGISTRY ] : Failed to save physical file: {e}")

        # 2. Save JSON metadata for UI/Download tracking (Local Cache)
        metadata = {
            "name": name,
            "mime": mime,
            "data": data_base64,
            "permanent_path": permanent_path,
            "token": token,
            "size": size,
            "session_id": session_id
        }
        
        file_path = os.path.join(self.temp_dir, f"{token}.json")
        with open(file_path, "w") as f:
            json.dump(metadata, f)
            
        # 3. Sync to Supabase for persistence
        try:
            from src.infrastructure.database.supabase_manager import get_supabase_manager
            db = get_supabase_manager()
            if db.enabled:
                db.insert("artifacts", {
                    "token": token,
                    "name": name,
                    "mime": mime,
                    "size": size,
                    "storage_path": permanent_path,
                    "session_id": session_id,
                    "metadata": {}
                })
                logger.info(f"[ REGISTRY ] : Synced artifact {token} to Supabase")
        except Exception as e:
            logger.warning(f"[ REGISTRY ] : Supabase sync failed: {e}")

        logger.info(f"[ REGISTRY ] : Stored metadata for {name} (Token: {token})")
        return token

    def get_file(self, token):
        file_path = os.path.join(self.temp_dir, f"{token}.json")
        logger.info(f"[ DEBUG ] : Looking up token={token} at {file_path}")
        
        if os.path.exists(file_path):
            try:
                with open(file_path, "r") as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"[ REGISTRY ] : Failed to read token {token}: {e}")

        # Fallback: scan permanent_dir for physical file matching the token
        if os.path.exists(self.permanent_dir):
            for filename in os.listdir(self.permanent_dir):
                parsed_token, display_name = self._parse_filename(filename)
                if parsed_token == token or token in filename:
                    full_path = os.path.join(self.permanent_dir, filename)
                    if os.path.isfile(full_path):
                        try:
                            with open(full_path, "rb") as f:
                                file_bytes = f.read()
                            import base64
                            import mimetypes
                            mime, _ = mimetypes.guess_type(filename)
                            if not mime:
                                _, ext = os.path.splitext(filename)
                                mime_mapping = {
                                    '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                                    '.doc': 'application/msword',
                                    '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                                    '.xls': 'application/vnd.ms-excel',
                                    '.csv': 'text/csv',
                                    '.html': 'text/html',
                                    '.htm': 'text/html',
                                    '.md': 'text/markdown',
                                    '.markdown': 'text/markdown',
                                    '.txt': 'text/plain',
                                    '.json': 'application/json',
                                    '.png': 'image/png',
                                    '.jpg': 'image/jpeg',
                                    '.jpeg': 'image/jpeg',
                                    '.pdf': 'application/pdf'
                                }
                                mime = mime_mapping.get(ext.lower(), 'application/octet-stream')

                            metadata = {
                                "name": display_name,
                                "mime": mime,
                                "data": base64.b64encode(file_bytes).decode('utf-8'),
                                "permanent_path": full_path,
                                "token": token,
                                "size": len(file_bytes),
                                "session_id": None
                            }
                            # Cache reconstructed metadata back to temp_dir
                            try:
                                with open(file_path, "w") as tf:
                                    json.dump(metadata, tf)
                            except Exception as ce:
                                logger.warning(f"[ REGISTRY ] : Failed to cache metadata for token {token}: {ce}")
                            return metadata
                        except Exception as e:
                            logger.error(f"[ REGISTRY ] : Failed to read physical file for token {token}: {e}")
                            
        logger.error(f"[ REGISTRY ] : Token {token} NOT FOUND on disk.")
        try:
            available = [f.split('.')[0] for f in os.listdir(self.temp_dir) if f.endswith('.json')]
            logger.info(f"[ DEBUG ] : Available tokens: {available}")
        except: pass
        return None

    def get_temp_path(self, token: str) -> str | None:
        """Return the internal disk path for a token's JSON file."""
        path = os.path.join(self.temp_dir, f"{token}.json")
        return path if os.path.exists(path) else None

    def list_artifacts(self):
        """List all physical artifacts in the permanent directory."""
        if not os.path.exists(self.permanent_dir):
            return []
            
        artifacts = []
        for filename in os.listdir(self.permanent_dir):
            file_path = os.path.join(self.permanent_dir, filename)
            if os.path.isfile(file_path):
                stats = os.stat(file_path)
                
                token, display_name = self._parse_filename(filename)
                
                artifacts.append({
                    "token": token,
                    "name": display_name,
                    "storage_name": filename,
                    "size": stats.st_size,
                    "created_at": stats.st_ctime,
                    "path": file_path
                })
        
        # Sort by creation time (newest first)
        artifacts.sort(key=lambda x: x['created_at'], reverse=True)
        return artifacts

file_registry = FileRegistry()
