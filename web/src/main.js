import { createApp } from "vue";
import ElementPlus from "element-plus";
import "element-plus/dist/index.css";
// Element Plus 官方暗色变量：html.dark 下自动切换组件配色
import "element-plus/theme-chalk/dark/css-vars.css";
import "./style.css";
import { initTheme } from "./theme";
import App from "./App.vue";

// 先定主题再挂载，避免首屏闪一下白底
initTheme();

createApp(App).use(ElementPlus).mount("#app");
