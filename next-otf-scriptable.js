const API_BASE = "https://next-otf-widget.onrender.com";
const NEXT_CLASS_URL = `${API_BASE}/api/next-class`;
const LOGIN_TEST_URL = `${API_BASE}/api/login-test`;

const KEY_EMAIL = "next_otf_email";
const KEY_PASSWORD = "next_otf_password";

async function showLoginPrompt(force = false) {
  let existingEmail = Keychain.contains(KEY_EMAIL) ? Keychain.get(KEY_EMAIL) : "";

  let alert = new Alert();
  alert.title = force ? "Reset OTF Login" : "Connect Orangetheory";
  alert.message =
    "Enter your Orangetheory email and password.\n\n" +
    "Your login is saved only on your iPhone in Scriptable’s secure Keychain. " +
    "It is sent over HTTPS only so the widget can fetch your next class. " +
    "The backend does not save your password.";

  alert.addTextField("OTF email", existingEmail);
  alert.addSecureTextField("OTF password", "");
  alert.addAction("Save & Test Login");
  alert.addCancelAction("Cancel");

  let result = await alert.presentAlert();

  if (result === -1) {
    throw new Error("Login cancelled.");
  }

  let email = alert.textFieldValue(0).trim();
  let password = alert.textFieldValue(1).trim();

  if (!email || !password) {
    throw new Error("Email and password are required.");
  }

  let req = new Request(LOGIN_TEST_URL);
  req.timeoutInterval = 20;
  req.headers = {
    "X-OTF-Email": email,
    "X-OTF-Password": password
  };

  let response = await req.loadJSON();

  if (!response.ok) {
    throw new Error(response.error || response.status || "Login failed.");
  }

  Keychain.set(KEY_EMAIL, email);
  Keychain.set(KEY_PASSWORD, password);

  return { email, password };
}

async function getCredentials() {
  if (Keychain.contains(KEY_EMAIL) && Keychain.contains(KEY_PASSWORD)) {
    return {
      email: Keychain.get(KEY_EMAIL),
      password: Keychain.get(KEY_PASSWORD)
    };
  }

  return await showLoginPrompt(false);
}

async function resetCredentials() {
  if (Keychain.contains(KEY_EMAIL)) Keychain.remove(KEY_EMAIL);
  if (Keychain.contains(KEY_PASSWORD)) Keychain.remove(KEY_PASSWORD);
  return await showLoginPrompt(true);
}

async function fetchData(email, password) {
  let req = new Request(NEXT_CLASS_URL);
  req.timeoutInterval = 20;
  req.headers = {
    "X-OTF-Email": email,
    "X-OTF-Password": password
  };

  return await req.loadJSON();
}

async function maybeShowManualMenu() {
  if (!config.runsInWidget) {
    let alert = new Alert();
    alert.title = "Next OTF";
    alert.message = "What would you like to do?";
    alert.addAction("Run Widget");
    alert.addAction("Reset Login");
    alert.addCancelAction("Cancel");

    let choice = await alert.presentAlert();

    if (choice === 1) {
      return "reset";
    }

    if (choice === -1) {
      return "cancel";
    }
  }

  return "run";
}

function randomItem(arr) {
  return arr[Math.floor(Math.random() * arr.length)];
}

const noClassPhrases = [
  "Go book your next burn.",
  "Future you wants a class booked.",
  "Book now. Brag later.",
  "The orange lights miss you.",
  "A better mood is one booking away.",
  "Go claim your treadmill.",
  "Don’t think. Book.",
  "Pick your pain. Book your gain.",
  "Your calendar has room. Your excuses do not.",
  "Your coach says book the class before your excuses start stretching.",
  "Your future self is already proud. Present you needs to tap book.",
  "Your splat points are not going to earn themselves.",
  "Stop negotiating with the couch.",
  "Hydrate, book, and stop being dramatic.",
  "This widget is judging you lovingly.",
  "If you can read this, you can book a class.",
  "No class booked is a bold strategy.",
  "The rower misses your questionable form.",
  "Excuses burn zero calories.",
  "The hardest rep is booking the class.",
  "Your next class is not going to book itself.",
  "The workout is optional. The regret is not.",
  "One tap. One class. Better day.",
  "Give your future self something to brag about.",
  "The coach photo is here. Your booking is not."
];

function pickNoClassPhrase() {
  return randomItem(noClassPhrases);
}

function createErrorWidget(message) {
  let widget = new ListWidget();
  widget.backgroundColor = new Color("#f26a21");
  widget.setPadding(14, 14, 14, 14);

  let title = widget.addText("NEXT OTF");
  title.font = Font.heavySystemFont(13);
  title.textColor = Color.white();

  widget.addSpacer(10);

  let main = widget.addText("Login needed");
  main.font = Font.heavySystemFont(22);
  main.textColor = Color.white();

  widget.addSpacer(6);

  let detail = widget.addText(message || "Open Scriptable and reset your login.");
  detail.font = Font.semiboldSystemFont(13);
  detail.textColor = Color.white();
  detail.lineLimit = 4;
  detail.minimumScaleFactor = 0.6;

  return widget;
}

async function createWidget(data) {
  const isWaitlist = (data.status || "").toLowerCase().includes("waitlist");
  const hasClass = data.has_class !== false && data.class_name;
  const coachImages = data.coach_images || {};

  let selectedNoClassPhrase = null;
  let noClassBackgroundUrl = null;

  if (!hasClass) {
    selectedNoClassPhrase = pickNoClassPhrase();

    // Priority order:
    // 1. Backend-provided top 3 most frequently booked coach photos
    // 2. Backend-selected coach image
    // 3. Any available coach image as a last fallback
    const topCoachImageUrls = data.top_coach_image_urls || [];

    if (topCoachImageUrls.length) {
      noClassBackgroundUrl = randomItem(topCoachImageUrls);
    } else if (data.coach_image_url) {
      noClassBackgroundUrl = data.coach_image_url;
    } else {
      const imageUrls = Object.keys(coachImages)
        .map(k => coachImages[k])
        .filter(Boolean);

      noClassBackgroundUrl = imageUrls.length ? randomItem(imageUrls) : null;
    }
  }

  const orange = new Color("#f26a21");
  const white = Color.white();
  const softWhite = new Color("#fff4ec");

  const waitlistBlueTop = new Color("#0b3f7a", 0.74);
  const waitlistBlueMid = new Color("#082542", 0.68);
  const waitlistBlueBottom = new Color("#020812", 0.84);

  const orangeTop = new Color("#f26a21", 0.66);
  const orangeMid = new Color("#000000", 0.52);
  const orangeBottom = new Color("#000000", 0.82);

  const pillColor = isWaitlist
    ? new Color("#061b34", 0.80)
    : new Color("#111111", 0.74);

  let widget = new ListWidget();
  widget.setPadding(0, 0, 0, 0);

  let backgroundUrl = hasClass ? data.coach_image_url : noClassBackgroundUrl;

  if (backgroundUrl) {
    try {
      let bgReq = new Request(backgroundUrl);
      bgReq.timeoutInterval = 4;
      let bgImage = await bgReq.loadImage();
      widget.backgroundImage = bgImage;
    } catch (e) {
      widget.backgroundColor = isWaitlist ? new Color("#061b34") : orange;
    }
  } else {
    widget.backgroundColor = isWaitlist ? new Color("#061b34") : orange;
  }

  let gradient = new LinearGradient();
  gradient.locations = [0, 0.44, 1];

  if (isWaitlist) {
    gradient.colors = [waitlistBlueTop, waitlistBlueMid, waitlistBlueBottom];
  } else {
    gradient.colors = [orangeTop, orangeMid, orangeBottom];
  }

  widget.backgroundGradient = gradient;

  let wrapper = widget.addStack();
  wrapper.layoutVertically();
  wrapper.setPadding(8, 14, 12, 14);

  let top = wrapper.addStack();
  top.layoutHorizontally();
  top.centerAlignContent();

  let statusPill = top.addStack();
  statusPill.backgroundColor = pillColor;
  statusPill.cornerRadius = 15;
  statusPill.setPadding(5, 10, 5, 10);

  let displayStatus = hasClass ? (data.status || "Unknown") : "BOOK NOW";
  let statusText = statusPill.addText(displayStatus);
  statusText.font = Font.heavySystemFont(15);
  statusText.textColor = white;
  statusText.lineLimit = 1;
  statusText.minimumScaleFactor = 0.7;

  top.addSpacer();

  let checked = top.addText(data.last_checked ? `↻ ${data.last_checked}` : "");
  checked.font = Font.mediumSystemFont(10);
  checked.textColor = white;
  checked.textOpacity = 0.82;

  wrapper.addSpacer(10);

  if (hasClass) {
    let className = wrapper.addText(data.class_name || "No upcoming class");
    className.font = Font.heavySystemFont(23);
    className.textColor = white;
    className.lineLimit = 2;
    className.minimumScaleFactor = 0.62;
    className.shadowColor = new Color("#000000", 0.58);
    className.shadowRadius = 3;
    className.shadowOffset = new Point(0, 1);

    wrapper.addSpacer(6);

    let whenText = wrapper.addText(data.starts_display || "");
    whenText.font = Font.semiboldSystemFont(13);
    whenText.textColor = softWhite;
    whenText.lineLimit = 1;
    whenText.minimumScaleFactor = 0.75;
    whenText.shadowColor = new Color("#000000", 0.42);
    whenText.shadowRadius = 2;
    whenText.shadowOffset = new Point(0, 1);

    let studioText = wrapper.addText(data.studio || "");
    studioText.font = Font.semiboldSystemFont(12);
    studioText.textColor = softWhite;
    studioText.textOpacity = 0.96;
    studioText.lineLimit = 1;
    studioText.minimumScaleFactor = 0.75;
    studioText.shadowColor = new Color("#000000", 0.42);
    studioText.shadowRadius = 2;
    studioText.shadowOffset = new Point(0, 1);

    let coachLine = data.coach ? `Coach: ${data.coach}` : "";
    let coachText = wrapper.addText(coachLine);
    coachText.font = Font.systemFont(11);
    coachText.textColor = softWhite;
    coachText.textOpacity = 0.94;
    coachText.lineLimit = 1;
    coachText.minimumScaleFactor = 0.75;
    coachText.shadowColor = new Color("#000000", 0.42);
    coachText.shadowRadius = 2;
    coachText.shadowOffset = new Point(0, 1);
  } else {
    let phrase = wrapper.addText(selectedNoClassPhrase || "Book your next class.");
    phrase.font = Font.heavySystemFont(16);
    phrase.textColor = white;
    phrase.lineLimit = 4;
    phrase.minimumScaleFactor = 0.42;
    phrase.shadowColor = new Color("#000000", 0.60);
    phrase.shadowRadius = 3;
    phrase.shadowOffset = new Point(0, 1);

    wrapper.addSpacer(5);

    let sub = wrapper.addText("No upcoming class.");
    sub.font = Font.semiboldSystemFont(12);
    sub.textColor = softWhite;
    sub.textOpacity = 0.95;
    sub.lineLimit = 2;
    sub.minimumScaleFactor = 0.8;
    sub.shadowColor = new Color("#000000", 0.42);
    sub.shadowRadius = 2;
    sub.shadowOffset = new Point(0, 1);
  }

  wrapper.addSpacer();

  let bottom = wrapper.addStack();
  bottom.layoutHorizontally();
  bottom.centerAlignContent();

  if (data.lifetime_classes !== null && data.lifetime_classes !== undefined) {
    let lifetimeBox = bottom.addStack();
    lifetimeBox.backgroundColor = new Color("#ffffff", 0.18);
    lifetimeBox.cornerRadius = 12;
    lifetimeBox.setPadding(6, 9, 6, 9);

    let lifetime = lifetimeBox.addText(`${data.lifetime_classes.toLocaleString()} classes`);
    lifetime.font = Font.heavySystemFont(13);
    lifetime.textColor = white;
    lifetime.lineLimit = 1;
    lifetime.minimumScaleFactor = 0.8;
  }

  bottom.addSpacer();

  let brandIcon = bottom.addText(isWaitlist ? "🕒" : "🍊");
  brandIcon.font = Font.systemFont(18);

  return widget;
}

let action = await maybeShowManualMenu();

if (action === "cancel") {
  Script.complete();
} else {
  try {
    let creds = action === "reset" ? await resetCredentials() : await getCredentials();
    let data = await fetchData(creds.email, creds.password);

    if (data.status === "Credentials required" || data.status === "Error") {
      throw new Error(data.error || data.message || data.status);
    }

    let widget = await createWidget(data);
    Script.setWidget(widget);

    if (!config.runsInWidget) {
      widget.presentMedium();
    }
  } catch (e) {
    let widget = createErrorWidget(e.message);
    Script.setWidget(widget);

    if (!config.runsInWidget) {
      widget.presentMedium();
    }
  }

  Script.complete();
}
