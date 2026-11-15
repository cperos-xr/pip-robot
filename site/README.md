# The project's web page

One static page: `index.html` and two photos in `img/`. No build step, no tracking, nothing to install.

## Before you publish it

Open `index.html` and fill in the addresses near the top of the `<script>` block (`LINKS`): the code on GitHub,
the printable body on Thingiverse, the robot's Bluesky profile, a video of it. A button whose address is empty says "soon".

## Putting it online

Any static host works. Two easy ones:

- **GitHub Pages** (free): `.github/workflows/pages.yml` publishes this folder on every push that changes it, in a
  public repo (a private repo skips it). One-time: Settings > Pages > Source: "GitHub Actions". For your own address:
  Settings > Pages > Custom domain (for example `pip.example.com`), then a DNS `CNAME` record that points that name at
  `<your-github-name>.github.io`.
- **Cloudflare Pages** (free): Workers & Pages > Create > Pages > upload the `site` folder (or connect the repo with
  build output directory `site`), then Custom domains > add your subdomain.

## Keep it apart from your own robot

This page is for everybody. Your own robot's web app is for you. Give them different addresses, and do not link from
here to your robot's sign-in page. If both live under the same domain, strangers will guess the other name, so put
your robot's address behind an access gate (see `SECURITY.md`).

## Changing it

The page is plain HTML and CSS. The face in the hero is drawn by the script at the bottom, the same way the robot's
touchscreen draws it. The icons are 12 x 12 pixel maps in `ICONS`. Photos: keep them free of people, mail, screens and
anything that shows where you live, and strip their metadata (`exiftool -all= photo.jpg`).
