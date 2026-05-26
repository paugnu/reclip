from unittest.mock import patch

from app import parse_spotify_url, is_spotify_url, run_spotify_download, jobs


def test_spotify_track_url_detection():
    assert is_spotify_url('https://open.spotify.com/track/123abc')


def test_spotify_album_url_detection():
    assert is_spotify_url('https://open.spotify.com/album/123abc?si=foo')


def test_spotify_playlist_url_detection():
    assert is_spotify_url('https://open.spotify.com/playlist/123abc')


def test_spotify_intl_track_url_detection():
    parsed = parse_spotify_url('https://open.spotify.com/intl-es/track/123abc?si=1')
    assert parsed == {'type': 'track', 'id': '123abc'}


def test_non_spotify_url_detection():
    assert not is_spotify_url('https://youtube.com/watch?v=abc')


@patch('app.shutil.which', return_value=None)
def test_spotdl_missing(mock_which):
    job_id = 'job-missing'
    jobs[job_id] = {'status': 'queued'}
    run_spotify_download(job_id, 'https://open.spotify.com/track/123abc', {'type': 'track', 'id': '123abc'})
    assert jobs[job_id]['status'] == 'error'
    assert 'Spotify support requires spotDL' in jobs[job_id]['error']


@patch('app.shutil.which', return_value='/usr/bin/spotdl')
@patch('app.glob.glob')
@patch('app.os.path.isfile', return_value=True)
@patch('app.subprocess.run')
def test_spotdl_success_single_file(mock_run, mock_isfile, mock_glob, mock_which):
    class Res:
        returncode = 0
        stdout = 'ok'
        stderr = ''

    mock_run.return_value = Res()
    mock_glob.side_effect = [[], ['/tmp/a.mp3']]

    job_id = 'job-success'
    jobs[job_id] = {'status': 'queued'}
    run_spotify_download(job_id, 'https://open.spotify.com/track/123abc', {'type': 'track', 'id': '123abc'})

    assert jobs[job_id]['status'] == 'done'
    assert jobs[job_id]['file'] == '/tmp/a.mp3'


@patch('app.shutil.which', return_value='/usr/bin/spotdl')
@patch('app.subprocess.run')
def test_spotdl_error(mock_run, mock_which):
    class Res:
        returncode = 1
        stdout = ''
        stderr = 'boom'

    mock_run.return_value = Res()
    job_id = 'job-error'
    jobs[job_id] = {'status': 'queued'}
    run_spotify_download(job_id, 'https://open.spotify.com/track/123abc', {'type': 'track', 'id': '123abc'})

    assert jobs[job_id]['status'] == 'error'
    assert jobs[job_id]['error'] == 'boom'


@patch('app.shutil.which', return_value='/usr/bin/spotdl')
@patch('app.glob.glob')
@patch('app.os.path.isfile', return_value=True)
@patch('app.subprocess.run')
def test_spotdl_playlist_multiple_files(mock_run, mock_isfile, mock_glob, mock_which):
    class Res:
        returncode = 0
        stdout = 'ok'
        stderr = ''

    mock_run.return_value = Res()
    mock_glob.side_effect = [[], ['/tmp/a.mp3', '/tmp/b.mp3']]

    job_id = 'job-multi'
    jobs[job_id] = {'status': 'queued'}
    run_spotify_download(job_id, 'https://open.spotify.com/playlist/123abc', {'type': 'playlist', 'id': '123abc'})

    assert jobs[job_id]['status'] == 'done'
    assert len(jobs[job_id]['files']) == 2
