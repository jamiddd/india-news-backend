// Firebase config for the web app (same project as the Android app, "Open Indian News Web" under
// Project settings -> Your apps). Used for sign-in and for web push (FCM). All of these values are
// public by design: access is enforced by Firebase Auth and the backend.
// Google sign-in also needs openindiannews.com under Firebase console -> Authentication ->
// Settings -> Authorized domains.
export default {
  apiKey: "AIzaSyAS-qoQKaQhs2rsORdWnpjOhkAOkh65-EA",
  authDomain: "indian-news-open.firebaseapp.com",
  projectId: "indian-news-open",
  storageBucket: "indian-news-open.firebasestorage.app",
  messagingSenderId: "389055722716",
  appId: "1:389055722716:web:201690a918dad3d4f17019",
};

// Public Web Push key (Firebase console -> Project settings -> Cloud Messaging -> Web configuration
// -> Web Push certificates). Empty switches web push off.
export const vapidKey = "BC_2QHOOTVknu_huDGnpIBZR5axQ9pztLwe1kSFveA3rnLFdkzUBvOEHCct2176fDtl6cKvGsqq2hBRlB1nvKnA";
