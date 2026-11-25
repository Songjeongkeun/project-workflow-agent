import { createBrowserRouter, Navigate, RouterProvider } from "react-router-dom";
import AppLayout from "./layouts/applayout/AppLayout";
import ProjectLayout from "./layouts/projectlayout/ProjectLayout";
import LoginPage from "./pages/login/LoginPage";
import ProjectsPage from "./pages/projects/ProjectsPage";
import ChatPage from "./pages/chat/ChatPage";
import PlanningPage from "./pages/planning/PlanningPage";
import RunStatusPage from "./pages/runstatus/RunStatusPage";
import RunsPage from "./pages/runs/RunsPage";
import ChangeRequestsPage from "./pages/changerequests/ChangeRequestsPage";
import MembersPage from "./pages/members/MembersPage";
import ApprovalsPage from "./pages/approvals/ApprovalsPage";
import ApprovalDetailPage from "./pages/approvaldetail/ApprovalDetailPage";
import NotFound from "./pages/notfound/NotFound";

// 화면 10개. 프로젝트를 고르면 실행 상태가 먼저 열린다.
const router = createBrowserRouter([
  {
    path: "/",
    errorElement: <NotFound />,
    children: [
      { index: true, element: <Navigate to="/projects" replace /> },
      { path: "login", element: <LoginPage /> },
      {
        // 로그인한 사용자만 보는 화면 (왼쪽 메뉴 포함)
        element: <AppLayout />,
        children: [
          { path: "projects", element: <ProjectsPage /> },
          {
            path: "projects/:pid",
            element: <ProjectLayout />,
            children: [
              { index: true, element: <RunStatusPage /> },
              { path: "chat", element: <ChatPage /> },
              { path: "planning", element: <PlanningPage /> },
              { path: "runs", element: <RunsPage /> },
              { path: "runs/:rid", element: <RunsPage /> },
              { path: "changes", element: <ChangeRequestsPage /> },
              { path: "changes/:cid", element: <ChangeRequestsPage /> },
              { path: "members", element: <MembersPage /> },
            ],
          },
          { path: "approvals", element: <ApprovalsPage /> },
          { path: "approvals/:aid", element: <ApprovalDetailPage /> },
        ],
      },
    ],
  },
]);

export default function App() {
  return <RouterProvider router={router} />;
}
