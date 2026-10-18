# frontend-vendor-assets Specification

## Purpose
前端关键第三方资源（样式框架、交互脚本、markdown 渲染库）由应用本地静态目录提供，使页面在无外网或公网 CDN 不可达的环境下样式与交互完整可用。

## Requirements

### Requirement: 关键前端资源本地提供

Bootstrap 样式与交互脚本、markdown 渲染库必须由应用自身静态目录（`static/vendor/`）经本应用静态路由提供，不得依赖任何第三方公网 CDN 才能保证页面渲染与交互。

#### Scenario: CDN 不可达时页面完整可用

- **WHEN** 公网 CDN（jsdelivr 等）完全不可达或设备离线，用户打开登录页或任意继承 base 布局的页面
- **THEN** 页面样式完整（布局、按钮、导航外观正常），且 Bootstrap 驱动的交互（下拉菜单、弹层、折叠）可正常触发

#### Scenario: 本地 vendor 资源可经应用获取

- **WHEN** 浏览器请求应用静态路由下的 bootstrap 样式、bootstrap 交互脚本、markdown 渲染库三个 vendor 文件
- **THEN** 均返回 200 且内容为对应库的完整文件

### Requirement: 模板不依赖境外 CDN 关键资源

页面模板中不得存在对第三方公网 CDN 关键渲染/交互资源（Bootstrap、markdown 库）的引用；Google Fonts 等纯增强型资源不受此约束。

#### Scenario: 无 CDN 关键资源引用残留

- **WHEN** 扫描全部页面模板中的脚本与样式引用
- **THEN** 不存在指向 jsdelivr 等 CDN 的 Bootstrap 或 markdown 库引用
