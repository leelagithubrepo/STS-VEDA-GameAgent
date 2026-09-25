(() => {
  const film = document.getElementById('veda-home-film');
  const sting = document.getElementById('veda-home-sting');
  const controls = document.getElementById('hero-media-controls');
  const motion = document.getElementById('hero-motion-toggle');
  const sound = document.getElementById('hero-sound-toggle');
  const status = document.getElementById('hero-media-status');
  const hero = document.querySelector('.cinematic-hero');
  if (!film || !sting || !controls || !motion || !sound || !status || !hero) return;

  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
  let resumeFilm = !reducedMotion.matches && !navigator.connection?.saveData;
  let finished = false;
  let filmFailed = false;
  let stingFailed = false;
  film.muted = true;
  film.loop = false;
  film.volume = 0.8;
  sting.volume = 0.8;
  controls.hidden = false;

  const syncControls = () => {
    motion.disabled = filmFailed;
    sound.disabled = reducedMotion.matches ? stingFailed : filmFailed;
    motion.textContent = film.paused ? (finished ? 'Replay animation' : 'Play animation') : 'Pause animation';
    motion.setAttribute('aria-label', motion.textContent);
    sound.textContent = reducedMotion.matches
      ? (sting.paused ? 'Play sound' : 'Stop sound')
      : (film.muted ? 'Play intro with sound' : 'Mute sound');
    sound.setAttribute('aria-label', sound.textContent);
  };
  const play = async media => {
    status.textContent = '';
    try { await media.play(); }
    catch (_) {
      film.muted = true;
      status.textContent = 'Playback could not start. Please try the play control again.';
    }
    syncControls();
  };
  for (const media of [film, sting]) {
    for (const event of ['play', 'pause', 'volumechange']) media.addEventListener(event, syncControls);
  }
  film.addEventListener('ended', () => {
    finished = true;
    film.muted = true;
    film.currentTime = 8.9;
    film.pause();
    syncControls();
  });
  sting.addEventListener('ended', syncControls);
  film.addEventListener('error', () => {
    status.textContent = 'The intro could not load. You can still explore VEDA below.';
    filmFailed = true;
    syncControls();
  });
  sting.addEventListener('error', () => {
    status.textContent = 'The sound could not load. You can still explore VEDA below.';
    stingFailed = true;
    syncControls();
  });
  motion.addEventListener('click', async () => {
    if (!film.paused) { film.pause(); return; }
    if (finished) { finished = false; film.currentTime = 0; }
    await play(film);
  });
  sound.addEventListener('click', async () => {
    if (reducedMotion.matches) {
      if (!sting.paused) sting.pause();
      else { sting.currentTime = 0; await play(sting); }
      return;
    }
    if (!film.muted) { film.muted = true; syncControls(); return; }
    finished = false;
    film.currentTime = 0;
    film.loop = false;
    film.muted = false;
    await play(film);
  });
  reducedMotion.addEventListener('change', event => {
    sting.pause();
    if (event.matches) {
      film.pause();
      film.muted = true;
      resumeFilm = false;
    }
    syncControls();
  });
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) {
      resumeFilm = !film.paused;
      film.pause();
      sting.pause();
    } else if (resumeFilm) {
      resumeFilm = false;
      void play(film);
    }
  });
  syncControls();
  if (!document.hidden && resumeFilm) {
    resumeFilm = false;
    void play(film);
  }
})();
