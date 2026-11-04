// Firebase config for web sign-in (same project as the Android app). Auth only needs the API
// key, auth domain and project id. The key is public by design (it identifies the project; access
// is enforced by Firebase Auth and the backend) and is the same one shipped inside the Android app.
// Google sign-in also needs openindiannews.com under Firebase console -> Authentication ->
// Settings -> Authorized domains. A registered web app (appId) is optional, for analytics only.
export default {
  apiKey: "AIzaSyCGysi6MNx-PuCpraFxdClYirW2Ewy5N4o",
  authDomain: "indian-news-open.firebaseapp.com",
  projectId: "indian-news-open",
  appId: "",
  messagingSenderId: "389055722716",
};
