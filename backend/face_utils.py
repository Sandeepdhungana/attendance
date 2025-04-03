import numpy as np
import insightface
from insightface.app import FaceAnalysis
import cv2
import json
import logging

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class FaceRecognition:
    def __init__(self):
        try:
            logger.info("Initializing FaceRecognition with buffalo_l model")
            self.app = FaceAnalysis(name='buffalo_l')
            self.app.prepare(ctx_id=0, det_size=(640, 640))
            self.threshold = 0.5 # Cosine similarity threshold for matching
            logger.info("FaceRecognition initialized successfully")
        except Exception as e:
            logger.error(f"Error initializing FaceRecognition: {str(e)}")
            raise

    def get_embeddings(self, image):
        """Extract face embeddings from image for all detected faces"""
        try:
            logger.info("Detecting faces in image")
            faces = self.app.get(image)
            if not faces:
                logger.warning("No faces detected in image")
                return []
            
            logger.info(f"Found {len(faces)} faces")
            # Return all face embeddings
            return [(face.embedding, face.bbox) for face in faces]
        except Exception as e:
            logger.error(f"Error extracting face embeddings: {str(e)}")
            return []

    def get_embedding(self, image):
        """Extract face embedding from image (legacy method for backward compatibility)"""
        embeddings = self.get_embeddings(image)
        if not embeddings:
            return None
        logger.info(f"Found {len(embeddings)} faces, using the first one")
        return embeddings[0][0]  # Return just the embedding of the first face

    def compare_faces(self, embedding1, embedding2):
        """Compare two face embeddings using cosine similarity"""
        if embedding1 is None or embedding2 is None:
            return 0.0  # Return 0 similarity for invalid embeddings
        
        try:
            # Normalize the embeddings
            embedding1_norm = embedding1 / np.linalg.norm(embedding1)
            embedding2_norm = embedding2 / np.linalg.norm(embedding2)
            
            # Calculate cosine similarity
            similarity = np.dot(embedding1_norm, embedding2_norm)
            
            logger.info(f"Face comparison similarity: {similarity}")
            return similarity
        except Exception as e:
            logger.error(f"Error comparing faces: {str(e)}")
            return 0.0

    def embedding_to_str(self, embedding):
        """Convert numpy array to string for storage"""
        try:
            return json.dumps(embedding.tolist())
        except Exception as e:
            logger.error(f"Error converting embedding to string: {str(e)}")
            raise

    def str_to_embedding(self, embedding_str):
        """Convert stored string back to numpy array"""
        try:
            return np.array(json.loads(embedding_str))
        except Exception as e:
            logger.error(f"Error converting string to embedding: {str(e)}")
            raise

    def find_match(self, query_embedding, stored_embeddings, threshold=None):
        """Find the best matching face from stored embeddings"""
        if threshold is None:
            threshold = self.threshold

        best_match = None
        best_similarity = 0.0  # For cosine similarity, higher is better

        for stored_embedding in stored_embeddings:
            similarity = self.compare_faces(query_embedding, stored_embedding)
            if similarity > best_similarity:
                best_similarity = similarity
                best_match = stored_embedding

        # For cosine similarity, higher is better (more similar)
        if best_similarity >= threshold:
            logger.info(f"Found match with similarity {best_similarity} (threshold: {threshold})")
            return best_match, best_similarity
        
        logger.info(f"No match found, best similarity was {best_similarity} (threshold: {threshold})")
        return None, best_similarity
        
    def find_matches_for_embeddings(self, query_embeddings, users, threshold=None):
        """Find matches for multiple face embeddings"""
        if threshold is None:
            threshold = self.threshold
            
        matches = []
        
        for query_embedding, bbox in query_embeddings:
            best_match = None
            best_similarity = 0.0
            
            for user in users:
                stored_embedding = self.str_to_embedding(user.embedding)
                similarity = self.compare_faces(query_embedding, stored_embedding)
                
                if similarity > best_similarity:
                    best_similarity = similarity
                    best_match = user
            
            if best_similarity >= threshold and best_match:
                matches.append({
                    'user': best_match,
                    'similarity': best_similarity,
                    'bbox': bbox
                })
                
        return matches 