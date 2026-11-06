
    const $ = id => document.getElementById(id);
    let result = null, revision = 0, cooldownTimer = null;

    // 主题切换（Dark / Light Mode）
    const themeToggle = $('themeToggle');
    const themeIcon = $('themeIcon');
    const initTheme = () => {
      const saved = localStorage.getItem('theme') || (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
      document.documentElement.setAttribute('data-theme', saved);
      updateThemeIcon(saved);
    };
    const updateThemeIcon = (theme) => {
      if (theme === 'dark') {
        themeIcon.innerHTML = '<circle cx="12" cy="12" r="5"></circle><line x1="12" y1="1" x2="12" y2="3"></line><line x1="12" y1="21" x2="12" y2="23"></line><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"></line><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"></line><line x1="1" y1="12" x2="3" y2="12"></line><line x1="21" y1="12" x2="23" y2="12"></line><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"></line><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"></line>';
      } else {
        themeIcon.innerHTML = '<path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"></path>';
      }
    };
    themeToggle.onclick = () => {
      const current = document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
      document.documentElement.setAttribute('data-theme', current);
      localStorage.setItem('theme', current);
      updateThemeIcon(current);
    };
    initTheme();

    // 状态提示条控制
    function status(text, error = false, loading = false) {
      clearTimeout(cooldownTimer);
      cooldownTimer = null;
      const banner = $('statusBanner');
      const spinner = $('statusSpinner');
      const label = $('status');
      if (!text) {
        banner.classList.add('hidden');
        return;
      }
      banner.classList.remove('hidden', 'error', 'success');
      spinner.classList.toggle('hidden', !loading);
      if (error) banner.classList.add('error');
      else if (!loading && text) banner.classList.add('success');
      label.textContent = text;
    }

    function showError(error) {
      const seconds = Number(error.retryAfter || 0);
      if (!seconds) return status(error.message, true, false);
      const until = Date.now() + seconds * 1000;
      const tick = () => {
        const left = Math.max(0, Math.ceil((until - Date.now()) / 1000));
        status(left ? `YouTube 暂时限制访问，${left} 秒后可重试；其他平台仍可使用` : '等待已结束，可以重新尝试', !!left, false);
        if (left) cooldownTimer = setTimeout(tick, 1000);
      };
      tick();
    }

    // 智能提取核心链接并清洗追踪参数
    function extractAndCleanUrl(text) {
      if (!text) return '';
      const m = text.match(/https?:\/\/[^\s<>"“”'()]+(?:\([^\s<>"“”'()]+\)|[^\s`!()\[\]{};:'".,<>?«»“”‘’])/);
      if (!m) return text.trim();
      let url = m[0].replace(/[。，、！!？?）)\]｝};]+$/, '');
      try {
        const u = new URL(url);
        const dropParams = ['share_token', 'share_user_id', 'utm_source', 'utm_medium', 'utm_campaign', 'tt_from', 'sender_device', 'is_from_webapp', 'mid', 'scene'];
        dropParams.forEach(p => u.searchParams.delete(p));
        return u.toString();
      } catch {
        return url;
      }
    }

    // 本地历史记录管理（纯前端 LocalStorage，保护隐私）
    const HISTORY_KEY = 'media_parse_history_v1';
    function getHistory() {
      try {
        return JSON.parse(localStorage.getItem(HISTORY_KEY) || '[]');
      } catch {
        return [];
      }
    }
    function saveHistory(item) {
      try {
        let list = getHistory().filter(h => h.url !== item.url);
        list.unshift(item);
        if (list.length > 10) list = list.slice(0, 10);
        localStorage.setItem(HISTORY_KEY, JSON.stringify(list));
        renderHistory();
      } catch (e) {
        console.warn('保存历史失败', e);
      }
    }
    function clearHistory() {
      localStorage.removeItem(HISTORY_KEY);
      renderHistory();
    }
    function renderHistory() {
      const list = getHistory();
      const card = $('historyCard');
      const container = $('historyList');
      if (!list.length) {
        card.classList.add('hidden');
        container.replaceChildren();
        return;
      }
      card.classList.remove('hidden');
      container.replaceChildren();

      const platformMap = {
        douyin: '抖音', tiktok: 'TikTok',
        youtube: 'YouTube', twitter: 'X', instagram: 'Ins',
        weibo: '微博', facebook: 'FB'
      };

      list.forEach(item => {
        const div = document.createElement('div');
        div.className = 'history-item';
        div.title = '点击重新解析此作品';

        const info = document.createElement('div');
        info.className = 'history-info';

        const tag = document.createElement('span');
        tag.className = 'history-platform-tag';
        tag.textContent = platformMap[item.platform] || item.platform || '视频';

        const name = document.createElement('span');
        name.className = 'history-name';
        name.textContent = (item.author ? item.author + ' · ' : '') + (item.title || item.url);

        info.append(tag, name);

        const timeSpan = document.createElement('span');
        timeSpan.className = 'history-time';
        const d = new Date(item.time);
        timeSpan.textContent = `${d.getMonth()+1}/${d.getDate()} ${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}`;

        const reloadSpan = document.createElement('span');
        reloadSpan.className = 'history-btn-reload';
        reloadSpan.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="23 4 23 10 17 10"></polyline><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"></path></svg> 解析';

        div.append(info, timeSpan, reloadSpan);
        div.onclick = () => {
          $('text').value = item.url;
          $('parseButton').click();
          $('workspace').scrollIntoView({ behavior: 'smooth' });
        };
        container.appendChild(div);
      });
    }

    $('clearHistoryBtn').onclick = () => {
      if (confirm('确定要清空本地历史记录吗？')) {
        clearHistory();
      }
    };

    // 快捷操作：粘贴即自动解析
    $('pasteBtn').onclick = async () => {
      try {
        const text = await navigator.clipboard.readText();
        if (text) {
          const cleaned = extractAndCleanUrl(text);
          $('text').value = cleaned;
          status('已自动提取纯净链接，正在启动解析…', false, true);
          setTimeout(() => {
            $('parseButton').click();
          }, 200);
        }
      } catch (err) {
        $('text').focus();
        status('请手动粘贴 (Ctrl+V / Command+V)');
      }
    };
    $('clearBtn').onclick = () => {
      $('text').value = '';
      $('text').focus();
      status('');
    };

    // 监听手动粘贴自动清洗
    $('text').addEventListener('paste', () => {
      setTimeout(() => {
        const val = $('text').value;
        const cleaned = extractAndCleanUrl(val);
        if (cleaned && cleaned !== val) {
          $('text').value = cleaned;
          status('已自动提取有效链接');
          setTimeout(() => status(''), 2000);
        }
      }, 50);
    });

    // 快捷键提交：Ctrl + Enter 或 Cmd + Enter
    $('text').addEventListener('keydown', e => {
      if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
        e.preventDefault();
        $('parseButton').click();
      }
    });

    // 统一 API 请求封装
    async function api(path, body) {
      const r = await fetch(path, {
        method: body === undefined ? 'GET' : 'POST',
        headers: body === undefined ? {} : { 'Content-Type': 'application/json' },
        body: body === undefined ? undefined : JSON.stringify(body)
      });
      let d;
      try { d = await r.json(); } catch { throw Error('服务暂时不可用，请稍后重试'); }
      if (!r.ok) {
        if (r.status === 401) show(false);
        const error = Error(typeof d.detail === 'string' ? d.detail : '请求失败，请稍后重试');
        error.retryAfter = Number(r.headers.get('Retry-After') || 0);
        throw error;
      }
      return d;
    }

    function show(ok) {
      $('login').classList.toggle('hidden', ok);
      $('workspace').classList.toggle('hidden', !ok);
      $('logout').classList.add('hidden');
    }

    const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
    const escapeHtml = str => String(str || '').replace(/[&<>"']/g, m => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[m]));

    // 解析主流程
    $('parse').onsubmit = async e => {
      e.preventDefault();
      const rev = ++revision;
      $('parseButton').disabled = true;
      $('result').classList.add('hidden');
      status('正在解析作品数据，请稍候…', false, true);

      try {
        let d = await api('/api/parse', { text: $('text').value });
        if (d.job) {
          const job = d.job;
          const parseDeadline = Date.now() + 450000;
          while (Date.now() < parseDeadline) {
            if (rev !== revision) return;
            d = await api('/api/parse/' + job + '?wait=20');
            if (!d.pending) break;
          }
          if (d.pending) throw Error('解析耗时较长，请稍后重试');
        }
        if (rev !== revision) return;
        result = d;

        // 平台与徽章映射
        const platformMap = {
          douyin: '抖音', tiktok: 'TikTok',
          youtube: 'YouTube', twitter: 'X / Twitter', instagram: 'Instagram',
          weibo: '微博', facebook: 'Facebook',
          vimeo: 'Vimeo', dailymotion: 'Dailymotion'
        };
        const pEl = $('platform');
        pEl.textContent = platformMap[d.platform] || d.platform;
        pEl.className = 'platform-badge ' + (d.platform || '');

        $('title').textContent = d.title || '无标题作品';

        // 标签元信息组装
        const metaEl = $('meta');
        metaEl.replaceChildren();
        const addMeta = (icon, text) => {
          if (!text) return;
          const span = document.createElement('span');
          span.className = 'result-meta-item';
          span.textContent = icon + ' ' + text;
          metaEl.appendChild(span);
        };
        addMeta('👤', d.author);
        if (d.images) addMeta('🖼️', d.images + ' 张图片');
        if (d.videos && d.images) addMeta('🎬', d.videos + ' 个视频');
        else if (d.width && d.height) addMeta('📐', d.width + ' × ' + d.height);
        if (d.duration) addMeta('⏱️', d.duration + ' 秒');

        $('media').replaceChildren();
        $('files').replaceChildren();
        $('result').classList.remove('hidden');
        $('result').scrollIntoView({ behavior: 'smooth', block: 'nearest' });

        // 自动无缝进入下载和预览准备，无需用户二次点击
        await loadMedia(rev);
      } catch (err) {
        showError(err);
      } finally {
        $('parseButton').disabled = false;
      }
    };

    // 自动获取媒体与下载资源
    function createMediaRenderer(snapshot) {
        const platformMap = {
          douyin: '抖音', tiktok: 'TikTok',
          youtube: 'YouTube', twitter: 'X', instagram: 'Ins',
          weibo: '微博', facebook: 'Facebook',
          vimeo: 'Vimeo', dailymotion: 'Dailymotion'
        };
        const pName = platformMap[snapshot.platform] || snapshot.platform || 'Media';
        const cleanTitle = (snapshot.title || '').replace(/[\\/:*?"<>|\r\n\t]/g, '_').trim().slice(0, 30) || '作品';
        const cleanAuthor = (snapshot.author || '').replace(/[\\/:*?"<>|\r\n\t]/g, '_').trim().slice(0, 16);
        const timeTag = new Date().toISOString().slice(0, 19).replace(/[-:T]/g, '_');
        const randTag = Math.random().toString(36).substring(2, 6);

        const seen = new Set(), imageList = [];
        let gallery = null, batchBar = null, archiveUrl = null, videoBar = null, videoCount = 0;
        $('media').replaceChildren();
        $('files').replaceChildren();
        return d => {
        archiveUrl = d.zip_url || null;
        const doneFiles = (d.files || []).filter(f => f.state === 'done' && !seen.has(f.name));
        doneFiles.forEach(f => seen.add(f.name));




        const videoFiles = doneFiles.filter(f => f.kind === 'video');
        const imageFiles = doneFiles.filter(f => f.kind === 'image');
        const coverFiles = doneFiles.filter(f => f.kind === 'cover');
        const otherFiles = doneFiles.filter(f => f.kind !== 'video' && f.kind !== 'image' && f.kind !== 'cover');

        // 1. 视频作品渲染
        for (const vf of videoFiles) {
          const ext = vf.name.includes('.') ? '.' + vf.name.split('.').pop() : '.mp4';
          const segs = [`【${pName}】`];
          if (cleanAuthor) segs.push(cleanAuthor);
          segs.push(cleanTitle, vf.name.replace(/\.[^.]+$/, ''), `${timeTag}_${randTag}`);
          const dynamicName = segs.join('_') + ext;
          const downloadUrl = vf.url + (vf.url.includes('?') ? '&' : '?') + 'fn=' + encodeURIComponent(dynamicName);

          const v = document.createElement('video');
          v.controls = true;
          v.playsInline = true;
          v.preload = 'metadata';
          v.src = vf.url + '?preview=true';
          const existingCover = (d.files || []).find(f => f.kind === 'cover' && f.state === 'done');
          if (existingCover) {
            v.poster = existingCover.url + '?preview=true';
          }
          $('media').append(v);

          const row = document.createElement('div');
          row.className = 'file-item';
          row.innerHTML = `
            <div class="file-info">
              <div class="file-icon">🎬</div>
              <div class="file-details">
                <div class="file-name" title="${escapeHtml(dynamicName)}">${escapeHtml(dynamicName)}</div>
                <div class="file-size">${(vf.bytes / 1024 / 1024).toFixed(2)} MB · 平台公开视频</div>
              </div>
            </div>
            <div class="file-actions">
              <button type="button" class="btn-copy">复制直链</button>
              <a class="btn-download" href="${downloadUrl}" download="${escapeHtml(dynamicName)}">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path><polyline points="7 10 12 15 17 10"></polyline><line x1="12" y1="15" x2="12" y2="3"></line></svg>
                下载视频
              </a>
            </div>
          `;
          const copyBtn = row.querySelector('.btn-copy');
          copyBtn.onclick = () => {
            const fullUrl = new URL(downloadUrl, window.location.origin).href;
            navigator.clipboard.writeText(fullUrl).then(() => {
              copyBtn.textContent = '已复制！';
              setTimeout(() => { copyBtn.textContent = '复制直链'; }, 1800);
            });
          };
          $('files').append(row);
        }

        // 1.2 多视频作品：一键打包全部视频
        videoCount = (d.files || []).filter(f => f.kind === 'video' && f.state === 'done').length;
        if (videoCount > 1 && !videoBar) {
          videoBar = document.createElement('div');
          videoBar.className = 'batch-bar';
          videoBar.innerHTML = `
            <div class="batch-info">
              <div class="batch-title">
                <span>🎬 多视频作品</span>
                <span class="batch-badge"></span>
              </div>
              <div class="batch-hint">下方可逐个单独下载视频；也可点击右侧一键打包为 ZIP 压缩包</div>
            </div>
            <div class="batch-actions">
              <button type="button" class="btn-batch-zip">
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"></path><polyline points="3.27 6.96 12 12.01 20.73 6.96"></polyline><line x1="12" y1="22.08" x2="12" y2="12"></line></svg>
                <span>打包下载全部视频 (ZIP)</span>
              </button>
            </div>
          `;
          $('files').prepend(videoBar);
          const videoZipBtn = videoBar.querySelector('.btn-batch-zip');
          videoZipBtn.onclick = () => {
            if (!archiveUrl) return status('请等待视频准备完成后再打包', true);
            const originalHTML = videoZipBtn.innerHTML;
            const zipName = `【${pName}】${cleanAuthor ? cleanAuthor + '_' : ''}${cleanTitle}_共${videoCount}个视频_${timeTag}_${randTag}.zip`;
            const link = document.createElement('a');
            link.href = archiveUrl + '?kind=video&fn=' + encodeURIComponent(zipName);
            link.download = zipName;
            document.body.appendChild(link);
            link.click();
            link.remove();
            videoZipBtn.disabled = true;
            videoZipBtn.textContent = '打包下载已启动';
            setTimeout(() => { videoZipBtn.disabled = !archiveUrl; videoZipBtn.innerHTML = originalHTML; }, 2000);
          };
        }
        if (videoBar) videoBar.querySelector('.batch-badge').textContent = `共 ${videoCount} 个视频`;

        // 1.5 封面图渲染
        for (const cf of coverFiles) {
          const ext = cf.name.includes('.') ? '.' + cf.name.split('.').pop() : '.jpg';
          const segs = [`【${pName}】`];
          if (cleanAuthor) segs.push(cleanAuthor);
          segs.push(cleanTitle, '封面', `${timeTag}_${randTag}`);
          const dynamicName = segs.join('_') + ext;
          const downloadUrl = cf.url + (cf.url.includes('?') ? '&' : '?') + 'fn=' + encodeURIComponent(dynamicName);
          const previewUrl = cf.url + '?preview=true';

          // 为视频播放器挂载海报
          const videoPlayer = $('media').querySelector('video');
          if (videoPlayer && !videoPlayer.poster) {
            videoPlayer.poster = previewUrl;
          }

          const sizeStr = cf.bytes > 1024 * 1024 
            ? (cf.bytes / 1024 / 1024).toFixed(2) + ' MB' 
            : (cf.bytes / 1024).toFixed(1) + ' KB';

          const row = document.createElement('div');
          row.className = 'file-item file-item-cover';
          row.innerHTML = `
            <div class="file-info">
              <div class="file-icon file-icon-cover" style="cursor:pointer;overflow:hidden;padding:0;position:relative;" title="点击放大预览封面">
                <img src="${previewUrl}" alt="封面" style="width:100%;height:100%;object-fit:cover;border-radius:9px;display:block;">
              </div>
              <div class="file-details">
                <div class="file-name" title="${escapeHtml(dynamicName)}">${escapeHtml(dynamicName)}</div>
                <div class="file-size">${sizeStr} · 视频高清封面图</div>
              </div>
            </div>
            <div class="file-actions">
              <button type="button" class="btn-copy btn-preview-cover">预览封面</button>
              <button type="button" class="btn-copy btn-copy-cover">复制直链</button>
              <a class="btn-download" href="${downloadUrl}" download="${escapeHtml(dynamicName)}">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path><polyline points="7 10 12 15 17 10"></polyline><line x1="12" y1="15" x2="12" y2="3"></line></svg>
                下载封面
              </a>
            </div>
          `;
          const thumbEl = row.querySelector('.file-icon-cover');
          if (thumbEl) thumbEl.onclick = () => openLightbox(previewUrl);

          const previewBtn = row.querySelector('.btn-preview-cover');
          if (previewBtn) previewBtn.onclick = () => openLightbox(previewUrl);

          const copyBtn = row.querySelector('.btn-copy-cover');
          if (copyBtn) {
            copyBtn.onclick = () => {
              const fullUrl = new URL(downloadUrl, window.location.origin).href;
              navigator.clipboard.writeText(fullUrl).then(() => {
                copyBtn.textContent = '已复制！';
                setTimeout(() => { copyBtn.textContent = '复制直链'; }, 1800);
              });
            };
          }
          $('files').append(row);
        }

        // 2. 图集作品渲染
        if (imageFiles.length > 0) {
          const newImages = [];
          imageFiles.forEach((f) => {
            const idx = (d.files || []).filter(item => item.kind === 'image').findIndex(item => item.name === f.name);
            const ext = f.name.includes('.') ? '.' + f.name.split('.').pop() : '.jpg';
            const segs = [`【${pName}】`];
            if (cleanAuthor) segs.push(cleanAuthor);
            segs.push(cleanTitle, `P${String(idx + 1).padStart(2, '0')}`, `${timeTag}_${randTag}`);
            const dynamicName = segs.join('_') + ext;
            const downloadUrl = f.url + (f.url.includes('?') ? '&' : '?') + 'fn=' + encodeURIComponent(dynamicName);
            const readyImage = {
              index: idx + 1,
              name: dynamicName,
              url: downloadUrl,
              previewUrl: f.url + '?preview=true',
              bytes: f.bytes
            };
            imageList.push(readyImage);newImages.push(readyImage);
          });

          imageList.sort((a,b) => a.index-b.index);
          // 如果有多张图，在顶部展示批量操作栏
          if (imageList.length > 1 && !batchBar) {
            batchBar = document.createElement('div');
            batchBar.className = 'batch-bar';
            batchBar.innerHTML = `
              <div class="batch-info">
                <div class="batch-title">
                  <span>🖼️ 原图画廊</span>
                  <span class="batch-badge">共 ${imageList.length} 张高清原图</span>
                </div>
                <div class="batch-hint">下方可逐张单独下载原图；也可点击右侧一键打包为 ZIP 压缩包或批量复制直链</div>
              </div>
              <div class="batch-actions">
                <button type="button" class="btn-batch-copy" id="copyAllImgLinks">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>
                  复制全部直链
                </button>
                <button type="button" class="btn-batch-zip" id="batchZipBtn">
                  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"></path><polyline points="3.27 6.96 12 12.01 20.73 6.96"></polyline><line x1="12" y1="22.08" x2="12" y2="12"></line></svg>
                  <span>打包下载全部 (ZIP)</span>
                </button>
              </div>
            `;
            if (gallery) $('media').insertBefore(batchBar, gallery);
            else $('media').append(batchBar);

            const zipBtn = batchBar.querySelector('#batchZipBtn');
            const copyAllBtn = batchBar.querySelector('#copyAllImgLinks');

            copyAllBtn.onclick = () => {
              const allUrls = imageList.map(item => new URL(item.url, window.location.origin).href).join('\n');
              navigator.clipboard.writeText(allUrls).then(() => {
                copyAllBtn.textContent = `已复制全部 ${imageList.length} 张直链！`;
                setTimeout(() => {
                  copyAllBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg> 复制全部直链';
                }, 2000);
              });
            };

            zipBtn.onclick = () => {
              if (!archiveUrl) return status('请等待图片准备完成后再打包', true);
              const originalHTML = zipBtn.innerHTML;
              const zipName = `【${pName}】${cleanAuthor ? cleanAuthor + '_' : ''}${cleanTitle}_共${imageList.length}张原图_${timeTag}_${randTag}.zip`;
              const link = document.createElement('a');
              link.href = archiveUrl + '?fn=' + encodeURIComponent(zipName);
              link.download = zipName;
              document.body.appendChild(link);
              link.click();
              link.remove();
              zipBtn.disabled = true;
              zipBtn.textContent = '打包下载已启动';
              setTimeout(() => { zipBtn.disabled = !archiveUrl; zipBtn.innerHTML = originalHTML; }, 2000);
            };
          }

          // 画廊卡片网格
          if (!gallery) {
            gallery = document.createElement('div');
            gallery.className = 'images-gallery';
            $('media').append(gallery);
          }

          newImages.forEach(item => {
            const card = document.createElement('div');
            card.className = 'image-card';
            card.style.order = item.index;

            const sizeText = item.bytes ? (item.bytes / 1024 / 1024).toFixed(2) + ' MB' : '';

            card.innerHTML = `
              <div class="image-thumb-area" title="点击放大预览">
                <img loading="lazy" alt="${escapeHtml(item.name)}" src="${item.previewUrl}">
                <span class="image-zoom-hint">🔍 点击放大</span>
              </div>
              <div class="image-card-footer">
                <div class="image-card-meta">
                  <span class="image-index-badge">P${String(item.index).padStart(2, '0')}</span>
                  ${sizeText ? `<span class="image-size-hint">${sizeText}</span>` : ''}
                </div>
                <div class="image-card-btns">
                  <button type="button" class="btn-img-action btn-copy-card" title="复制直链">
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>
                  </button>
                  <a class="btn-img-action primary" href="${item.url}" download="${escapeHtml(item.name)}" title="下载单张原图">
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path><polyline points="7 10 12 15 17 10"></polyline><line x1="12" y1="15" x2="12" y2="3"></line></svg>
                    下载
                  </a>
                </div>
              </div>
            `;

            const thumb = card.querySelector('.image-thumb-area');
            thumb.onclick = () => openLightbox(item.previewUrl);

            const copyCardBtn = card.querySelector('.btn-copy-card');
            copyCardBtn.onclick = () => {
              const fullUrl = new URL(item.url, window.location.origin).href;
              navigator.clipboard.writeText(fullUrl).then(() => {
                copyCardBtn.innerHTML = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"></polyline></svg>';
                setTimeout(() => {
                  copyCardBtn.innerHTML = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>';
                }, 1500);
              });
            };

            gallery.appendChild(card);
          });

        }

        // 3. 其他文件（音频伴奏等）
        for (const ofile of otherFiles) {
          const ext = ofile.name.includes('.') ? '.' + ofile.name.split('.').pop() : '';
          const dynamicName = `【${pName}】${cleanAuthor ? cleanAuthor + '_' : ''}${cleanTitle}_${timeTag}_${randTag}${ext}`;
          const downloadUrl = ofile.url + (ofile.url.includes('?') ? '&' : '?') + 'fn=' + encodeURIComponent(dynamicName);

          const row = document.createElement('div');
          row.className = 'file-item';
          row.innerHTML = `
            <div class="file-info">
              <div class="file-icon">🎵</div>
              <div class="file-details">
                <div class="file-name" title="${escapeHtml(dynamicName)}">${escapeHtml(dynamicName)}</div>
                <div class="file-size">${(ofile.bytes / 1024 / 1024).toFixed(2)} MB · 伴奏/音频</div>
              </div>
            </div>
            <div class="file-actions">
              <button type="button" class="btn-copy">复制直链</button>
              <a class="btn-download" href="${downloadUrl}" download="${escapeHtml(dynamicName)}">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path><polyline points="7 10 12 15 17 10"></polyline><line x1="12" y1="15" x2="12" y2="3"></line></svg>
                下载
              </a>
            </div>
          `;
          const copyBtn = row.querySelector('.btn-copy');
          copyBtn.onclick = () => {
            const fullUrl = new URL(downloadUrl, window.location.origin).href;
            navigator.clipboard.writeText(fullUrl).then(() => {
              copyBtn.textContent = '已复制！';
              setTimeout(() => { copyBtn.textContent = '复制直链'; }, 1800);
            });
          };
          $('files').append(row);
        }

        if (batchBar) {
          const terminal = ['done','partial','failed','cancelled'].includes(d.state);
          const total = (d.files || []).filter(f => f.kind === 'image').length;
          batchBar.querySelector('.batch-badge').textContent = terminal ? `共 ${imageList.length} 张原图已就绪` : `${imageList.length} / ${total} 张已就绪`;
          batchBar.querySelector('#batchZipBtn').disabled = !terminal || !archiveUrl;
          batchBar.querySelector('#copyAllImgLinks').disabled = !terminal;
        }
        };
    }

    async function loadMedia(rev) {
      $('prepare').classList.add('hidden');
      $('prepare').disabled = true;
      status('作品信息解析完成，正在自动提取高清媒体资源…', false, true);
      const renderReady = createMediaRenderer(result);
      try {
        const { job } = await api('/api/download', { result_id: result.result_id });
        let d, version = '';
        const downloadDeadline = Date.now() + 4020000;
        while (Date.now() < downloadDeadline) {
          if (rev !== revision) return;
          d = await api('/api/download/' + job + '?wait=20&after=' + encodeURIComponent(version));
          if (rev !== revision) return;
          version = d.version || '';
          renderReady(d);
          const doneCount = (d.files || []).filter(f => f.state === 'done').length;
          status(`正在提取媒体资源… 已就绪 ${doneCount} / ${(d.files || []).length || 1} 个文件，已就绪资源可直接使用`, false, true);
          if (['done','partial','failed','cancelled'].includes(d.state)) break;
        }
        if (!d || !['done','partial','failed','cancelled'].includes(d.state)) throw Error('获取资源耗时较长，请点击重试');
        if (d.state === 'failed' || d.state === 'cancelled') {
          const error = Error(d.error || '媒体下载失败，请稍后重试');
          error.retryAfter = d.retry_after || 0;
          throw error;
        }
        if (!(d.files || []).some(f => f.state === 'done')) throw Error('未能提取到可用媒体流，请稍后重试');
        const failed = d.files.filter(f => f.state !== 'done');
        status(failed.length ? '部分文件准备就绪，可供下载' : '🎉 所有资源已准备完成，可直接预览或下载！', !!failed.length, false);

        // 成功后保存到本地历史记录
        saveHistory({
          platform: result.platform,
          title: result.title,
          author: result.author,
          url: $('text').value.trim(),
          time: Date.now()
        });
      } catch (err) {
        showError(err);
        $('prepare').classList.remove('hidden');
        $('prepare').disabled = false;
      }
    }

    $('prepare').onclick = () => loadMedia(revision);

    // Lightbox 大图预览控制
    const lightbox = $('lightbox');
    const lightboxImg = $('lightboxImg');
    const openLightbox = src => {
      lightboxImg.src = src;
      lightbox.classList.remove('hidden');
    };
    lightbox.onclick = () => {
      lightbox.classList.add('hidden');
      lightboxImg.src = '';
    };

    // 检查并初始化会话与本地历史记录
    renderHistory();
    api('/api/session').then(() => show(true)).catch(() => show(false));
  
