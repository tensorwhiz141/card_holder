
const toggle = document.getElementById("toggleTheme");
const body = document.getElementById("body");

if (localStorage.getItem("theme") === "dark") enableDark();

if (toggle) {
  toggle.addEventListener("click", () => {
    if (localStorage.getItem("theme") === "dark") {
      localStorage.setItem("theme", "light");
      disableDark();
    } else {
      localStorage.setItem("theme", "dark");
      enableDark();
    }
  });
}

function enableDark() {
  body.classList.add("bg-gray-900", "text-gray-100");
  body.classList.remove("from-blue-50", "to-indigo-100");
}

function disableDark() {
  body.classList.remove("bg-gray-900", "text-gray-100");
  body.classList.add("from-blue-50", "to-indigo-100");
}
