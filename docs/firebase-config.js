// Firebase configuration for GitHub Pages frontend
// This is public and safe to commit (API key is public; Firestore rules enforce security)
// Update with your Firebase project's public config from Firebase Console

const firebaseConfig = {
  apiKey: "AIzaSyBWzZBc1VCTP-_DKr75jY-IlljqUcDDXs8",
  authDomain: "tube-43be5.firebaseapp.com",
  projectId: "tube-43be5",
  storageBucket: "tube-43be5.firebasestorage.app",
  messagingSenderId: "191205749894",
  appId: "1:191205749894:web:9e537d90a9391caad44d07"
};

// Export for use in index.html
if (typeof module !== 'undefined' && module.exports) {
  module.exports = FIREBASE_CONFIG;
}
