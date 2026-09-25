(() => {
  const film = document.getElementById('veda-home-film');
  const music = document.getElementById('veda-home-music');
  const controls = document.getElementById('hero-media-controls');
  const motion = document.getElementById('hero-motion-toggle');
  const sound = document.getElementById('hero-sound-toggle');
  const status = document.getElementById('hero-media-status');
  if (!film || !music || !controls || !motion || !sound || !status) return;

  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
  let resumeFilm = !reducedMotion.matches && !navigator.connection?.saveData;
  let resumeMusic = false;
  film.muted = true;
  music.volume = 0.65;
  controls.hidden = false;

  const syncControls = () => {
    motion.textContent = film.paused ? 'Play animation' : 'Pause animation';
    motion.setAttribute('aria-label', film.paused ? 'Play hero animation' : 'Pause hero animation');
    sound.textContent = music.paused ? 'Music off' : 'Music on';
    sound.setAttribute('aria-pressed', String(!music.paused));
    sound.setAttribute('aria-label', music.paused ? 'Turn hero music on' : 'Turn hero music off');
  };
  const play = async (media) => {
    status.textContent = '';
    try {
      await media.play();
    } catch (_) {
      status.textContent = media === film
        ? 'Press Play animation to start the film.'
        : 'Music could not start. Press Music off to try again.';
    }
    syncControls();
  };
  for (const media of [film, music]) {
    media.addEventListener('play', syncControls);
    media.addEventListener('pause', syncControls);
  }
  film.addEventListener('error', () => {
    status.textContent = 'The film could not load. You can still explore VEDA below.';
    motion.disabled = true;
    syncControls();
  });
  music.addEventListener('error', () => {
    status.textContent = 'The music could not load. You can still watch the animation.';
    sound.disabled = true;
    syncControls();
  });
  motion.addEventListener('click', async () => {
    if (film.paused) await play(film);
    else film.pause();
  });
  sound.addEventListener('click', async () => {
    if (music.paused) await play(music);
    else music.pause();
  });
  reducedMotion.addEventListener('change', event => {
    if (event.matches) {
      film.pause();
      resumeFilm = false;
    }
  });
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) {
      resumeFilm = !film.paused;
      resumeMusic = !music.paused;
      film.pause();
      music.pause();
    } else {
      if (resumeFilm) void play(film);
      if (resumeMusic) void play(music);
      resumeFilm = false;
      resumeMusic = false;
    }
  });
  syncControls();
  if (!document.hidden && resumeFilm) {
    resumeFilm = false;
    void play(film);
  }
})();
