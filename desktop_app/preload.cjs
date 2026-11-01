'use strict';
const { contextBridge, ipcRenderer } = require('electron');

// Страница получает только конкретные операции, без доступа к Node и командам ОС.
contextBridge.exposeInMainWorld('vatesDesktop', Object.freeze({
  status: () => ipcRenderer.invoke('vates:status'),
  start: port => ipcRenderer.invoke('vates:start', port),
  stop: () => ipcRenderer.invoke('vates:stop'),
  generate: input => ipcRenderer.invoke('vates:generate', input),
  chooseProject: () => ipcRenderer.invoke('vates:choose-project'),
  openLogs: () => ipcRenderer.invoke('vates:open-logs'),
  onState: callback => {
    const listener = (_event, state) => callback(state);
    ipcRenderer.on('vates:state', listener);
    return () => ipcRenderer.removeListener('vates:state', listener);
  },
}));
