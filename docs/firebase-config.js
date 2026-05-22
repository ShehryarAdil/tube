// Firebase configuration for GitHub Pages frontend
// This is public and safe to commit (API key is public; Firestore rules enforce security)
// Update with your Firebase project's public config from Firebase Console

const FIREBASE_CONFIG = {
  apiKey: "YOUR_API_KEY_HERE",
  authDomain: "tube-56f4f.firebaseapp.com",
  projectId: "tube-56f4f",
  storageBucket: "tube-56f4f.appspot.com",
  messagingSenderId: "YOUR_MESSAGING_SENDER_ID",
  appId: "YOUR_APP_ID"
};

// Export for use in index.html
if (typeof module !== 'undefined' && module.exports) {
  module.exports = FIREBASE_CONFIG;
}
