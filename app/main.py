from datetime import datetime, timedelta, timezone
import os
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Query, status
from dotenv import load_dotenv
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, EmailStr
import jwt
from pwdlib import PasswordHash
from sqlalchemy.exc import IntegrityError
from sqlalchemy import case, func, inspect, text
from sqlalchemy.orm import Session

from app.database import Base, engine, get_db
from app import models

load_dotenv()

app = FastAPI(title="Circle API")
password_hash = PasswordHash.recommended()
bearer_scheme = HTTPBearer(auto_error=False)
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY")
if not JWT_SECRET_KEY:
    raise RuntimeError("JWT_SECRET_KEY must be configured")
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30


@app.on_event("startup")
def create_database_tables() -> None:
    Base.metadata.create_all(bind=engine)
    with engine.begin() as connection:
        user_columns = {
            column["name"] for column in inspect(connection).get_columns("users")
        }
        if "password_hash" not in user_columns:
            connection.execute(text("ALTER TABLE users ADD COLUMN password_hash VARCHAR(255)"))
        if "is_admin" not in user_columns:
            connection.execute(
                text("ALTER TABLE users ADD COLUMN is_admin BOOLEAN NOT NULL DEFAULT 0")
            )
        notification_columns = {
            column["name"]
            for column in inspect(connection).get_columns("notifications")
        }
        post_id_nullable = next(
            column["nullable"]
            for column in inspect(connection).get_columns("notifications")
            if column["name"] == "post_id"
        )
        if "type" in notification_columns and not post_id_nullable:
            connection.execute(text("ALTER TABLE notifications RENAME TO notifications_old"))
            connection.execute(
                text(
                    "CREATE TABLE notifications ("
                    "id INTEGER NOT NULL PRIMARY KEY, "
                    "recipient_id INTEGER NOT NULL, "
                    "actor_id INTEGER NOT NULL, "
                    "post_id INTEGER, "
                    "type VARCHAR(20) NOT NULL, "
                    "message VARCHAR(255) NOT NULL, "
                    "is_read BOOLEAN DEFAULT '0', "
                    "created_at DATETIME DEFAULT CURRENT_TIMESTAMP, "
                    "CONSTRAINT ck_notification_type CHECK "
                    "(type IN ('comment', 'vote', 'follow')), "
                    "FOREIGN KEY(recipient_id) REFERENCES users (id), "
                    "FOREIGN KEY(actor_id) REFERENCES users (id), "
                    "FOREIGN KEY(post_id) REFERENCES posts (id)"
                    ")"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO notifications "
                    "(id, recipient_id, actor_id, post_id, type, message, is_read, created_at) "
                    "SELECT id, recipient_id, actor_id, post_id, type, message, is_read, created_at "
                    "FROM notifications_old"
                )
            )
            connection.execute(text("DROP TABLE notifications_old"))
        notification_type_sql = text(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'notifications'"
        )
        notification_sql = connection.execute(notification_type_sql).scalar() or ""
        if "'follow'" not in notification_sql:
            raise RuntimeError("Notifications table migration did not add follow support")
        post_columns = {
            column["name"] for column in inspect(connection).get_columns("posts")
        }
        if "is_deleted" not in post_columns:
            connection.execute(
                text("ALTER TABLE posts ADD COLUMN is_deleted BOOLEAN NOT NULL DEFAULT 0")
            )
        if "deleted_at" not in post_columns:
            connection.execute(text("ALTER TABLE posts ADD COLUMN deleted_at DATETIME"))
        comment_columns = {
            column["name"] for column in inspect(connection).get_columns("comments")
        }
        if "is_deleted" not in comment_columns:
            connection.execute(
                text("ALTER TABLE comments ADD COLUMN is_deleted BOOLEAN NOT NULL DEFAULT 0")
            )
        if "deleted_at" not in comment_columns:
            connection.execute(text("ALTER TABLE comments ADD COLUMN deleted_at DATETIME"))


class UserCreate(BaseModel):
    username: str
    email: EmailStr


class RegistrationRequest(UserCreate):
    password: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    email: EmailStr
    created_at: datetime


class BlockedUserResponse(BaseModel):
    id: int
    username: str
    created_at: datetime


class FollowUserResponse(BaseModel):
    id: int
    username: str
    followed_at: datetime


class UserProfileResponse(BaseModel):
    id: int
    username: str
    created_at: datetime
    post_count: int
    comment_count: int
    karma: int
    follower_count: int
    following_count: int


class TokenResponse(BaseModel):
    access_token: str
    token_type: str


class CommunityCreate(BaseModel):
    name: str
    description: str


class CommunityResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str
    creator_id: int
    created_at: datetime
    member_count: int


class CommunityMemberResponse(BaseModel):
    id: int
    username: str
    joined_at: datetime


class PostCreate(BaseModel):
    title: str
    content: str
    community_id: int


class PostResponse(BaseModel):
    id: int
    title: str
    content: str
    author_id: int
    community_id: int
    created_at: datetime
    author_username: str
    community_name: str


class CommentCreate(BaseModel):
    content: str


class CommentResponse(BaseModel):
    id: int
    content: str
    author_id: int
    post_id: int
    created_at: datetime
    author_username: str


class VoteCreate(BaseModel):
    value: Literal[1, -1]


class VoteSummary(BaseModel):
    upvotes: int
    downvotes: int
    score: int


class FeedItem(BaseModel):
    id: int
    title: str
    content: str
    author_username: str
    community_name: str
    created_at: datetime
    upvotes: int
    downvotes: int
    score: int
    comment_count: int


class UserPostItem(BaseModel):
    id: int
    title: str
    content: str
    community_name: str
    created_at: datetime
    upvotes: int
    downvotes: int
    score: int
    comment_count: int


class SearchPostResult(BaseModel):
    id: int
    title: str
    content: str
    author_username: str
    community_name: str
    created_at: datetime
    score: int
    comment_count: int


class SearchCommunityResult(BaseModel):
    id: int
    name: str
    description: str
    creator_username: str
    created_at: datetime


class SearchUserResult(BaseModel):
    id: int
    username: str
    created_at: datetime


class NotificationResponse(BaseModel):
    id: int
    actor_username: str
    post_id: int | None
    type: Literal["comment", "vote", "follow"]
    message: str
    is_read: bool
    created_at: datetime


class ReportCreate(BaseModel):
    post_id: int | None = None
    comment_id: int | None = None
    reason: Literal[
        "spam",
        "harassment",
        "hate",
        "misinformation",
        "inappropriate",
        "other",
    ]


class ReportResponse(BaseModel):
    id: int
    target_type: Literal["post", "comment"]
    post_id: int | None
    comment_id: int | None
    reason: str
    status: Literal["pending", "reviewed", "dismissed", "actioned"]
    created_at: datetime


class AdminReportResponse(BaseModel):
    id: int
    reporter_username: str
    target_type: Literal["post", "comment"]
    post_id: int | None
    comment_id: int | None
    reason: str
    status: Literal["pending", "reviewed", "dismissed", "actioned"]
    created_at: datetime


class ReviewReportRequest(BaseModel):
    status: Literal["reviewed", "dismissed", "actioned"]


def create_access_token(user: models.User) -> str:
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {"sub": str(user.id), "email": user.email, "exp": expires_at}
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    database: Session = Depends(get_db),
) -> models.User:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        payload = jwt.decode(
            credentials.credentials,
            JWT_SECRET_KEY,
            algorithms=[JWT_ALGORITHM],
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user_id = payload.get("sub")
    if not isinstance(user_id, str) or not user_id.isdigit():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user = database.get(models.User, int(user_id))
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def get_current_admin(
    current_user: models.User = Depends(get_current_user),
) -> models.User:
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return current_user


def is_user_blocked(
    database: Session,
    blocker_id: int,
    blocked_id: int,
) -> bool:
    return (
        database.query(models.UserBlock)
        .filter(
            models.UserBlock.blocker_id == blocker_id,
            models.UserBlock.blocked_id == blocked_id,
        )
        .first()
        is not None
    )


def users_are_blocked(
    database: Session,
    first_user_id: int,
    second_user_id: int,
) -> bool:
    return is_user_blocked(database, first_user_id, second_user_id) or is_user_blocked(
        database, second_user_id, first_user_id
    )


def save_user(
    user: UserCreate | RegistrationRequest,
    database: Session,
    hashed_password: str | None = None,
) -> models.User:
    new_user = models.User(
        username=user.username,
        email=str(user.email),
        password_hash=hashed_password,
    )
    database.add(new_user)
    try:
        database.commit()
    except IntegrityError as error:
        database.rollback()
        constraint = str(error.orig)
        if "users.username" in constraint:
            detail = "Username already exists"
        elif "users.email" in constraint:
            detail = "Email already exists"
        else:
            raise
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)
    database.refresh(new_user)
    return new_user


def create_notification(
    database: Session,
    recipient_id: int,
    actor: models.User,
    post_id: int | None,
    notification_type: Literal["comment", "vote", "follow"],
    message: str,
) -> None:
    if recipient_id == actor.id:
        return
    if notification_type in ("vote", "follow"):
        existing = (
            database.query(models.Notification)
            .filter(
                models.Notification.recipient_id == recipient_id,
                models.Notification.actor_id == actor.id,
                models.Notification.post_id == post_id,
                models.Notification.type == notification_type,
            )
            .first()
        )
        if existing is not None:
            return
    database.add(
        models.Notification(
            recipient_id=recipient_id,
            actor_id=actor.id,
            post_id=post_id,
            type=notification_type,
            message=message,
        )
    )


@app.get("/")
def root() -> dict[str, str]:
    return {"message": "Circle API is running"}


@app.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}


@app.post(
    "/api/users",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_user(user: UserCreate, database: Session = Depends(get_db)) -> models.User:
    return save_user(user, database)


@app.get("/api/users", response_model=list[UserResponse])
def list_users(database: Session = Depends(get_db)) -> list[models.User]:
    return database.query(models.User).order_by(models.User.id).all()


@app.get("/api/users/blocked", response_model=list[BlockedUserResponse])
def list_blocked_users(
    database: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> list[BlockedUserResponse]:
    rows = (
        database.query(models.User)
        .join(models.UserBlock, models.UserBlock.blocked_id == models.User.id)
        .filter(models.UserBlock.blocker_id == current_user.id)
        .order_by(models.UserBlock.created_at.desc(), models.UserBlock.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return [
        BlockedUserResponse(
            id=user.id,
            username=user.username,
            created_at=user.created_at,
        )
        for user in rows
    ]


@app.post(
    "/api/users/{user_id}/follow",
    response_model=FollowUserResponse,
    status_code=status.HTTP_201_CREATED,
)
def follow_user(
    user_id: int,
    database: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> FollowUserResponse:
    target = database.get(models.User, user_id)
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    if target.id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Users cannot follow themselves",
        )
    if users_are_blocked(database, current_user.id, target.id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Following is blocked between these users",
        )
    existing_follow = (
        database.query(models.UserFollow)
        .filter(
            models.UserFollow.follower_id == current_user.id,
            models.UserFollow.following_id == target.id,
        )
        .first()
    )
    if existing_follow is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="User is already followed",
        )
    follow = models.UserFollow(
        follower_id=current_user.id,
        following_id=target.id,
    )
    database.add(follow)
    create_notification(
        database=database,
        recipient_id=target.id,
        actor=current_user,
        post_id=None,
        notification_type="follow",
        message=f"{current_user.username} followed you",
    )
    database.commit()
    database.refresh(follow)
    return FollowUserResponse(
        id=target.id,
        username=target.username,
        followed_at=follow.created_at,
    )


@app.delete("/api/users/{user_id}/follow")
def unfollow_user(
    user_id: int,
    database: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> dict[str, str]:
    target = database.get(models.User, user_id)
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    follow = (
        database.query(models.UserFollow)
        .filter(
            models.UserFollow.follower_id == current_user.id,
            models.UserFollow.following_id == target.id,
        )
        .first()
    )
    if follow is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User is not followed",
        )
    database.delete(follow)
    database.commit()
    return {"detail": "User unfollowed"}


def list_followers_or_following(
    database: Session,
    user_id: int,
    following: bool,
    limit: int,
    offset: int,
) -> list[FollowUserResponse]:
    if database.get(models.User, user_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    relation_column = (
        models.UserFollow.follower_id if following else models.UserFollow.following_id
    )
    join_column = (
        models.UserFollow.following_id if following else models.UserFollow.follower_id
    )
    rows = (
        database.query(models.User, models.UserFollow.created_at)
        .join(models.UserFollow, join_column == models.User.id)
        .filter(relation_column == user_id)
        .order_by(models.UserFollow.created_at.desc(), models.UserFollow.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return [
        FollowUserResponse(
            id=user.id,
            username=user.username,
            followed_at=followed_at,
        )
        for user, followed_at in rows
    ]


@app.get("/api/users/{user_id}/followers", response_model=list[FollowUserResponse])
def list_followers(
    user_id: int,
    database: Session = Depends(get_db),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> list[FollowUserResponse]:
    return list_followers_or_following(database, user_id, False, limit, offset)


@app.get("/api/users/{user_id}/following", response_model=list[FollowUserResponse])
def list_following(
    user_id: int,
    database: Session = Depends(get_db),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> list[FollowUserResponse]:
    return list_followers_or_following(database, user_id, True, limit, offset)


@app.post(
    "/api/users/{user_id}/block",
    response_model=BlockedUserResponse,
    status_code=status.HTTP_201_CREATED,
)
def block_user(
    user_id: int,
    database: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> BlockedUserResponse:
    target = database.get(models.User, user_id)
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    if target.id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Users cannot block themselves",
        )
    if is_user_blocked(database, current_user.id, target.id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="User is already blocked",
        )
    block = models.UserBlock(blocker_id=current_user.id, blocked_id=target.id)
    database.add(block)
    database.commit()
    return BlockedUserResponse(
        id=target.id,
        username=target.username,
        created_at=target.created_at,
    )


@app.delete("/api/users/{user_id}/block")
def unblock_user(
    user_id: int,
    database: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> dict[str, str]:
    target = database.get(models.User, user_id)
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    block = (
        database.query(models.UserBlock)
        .filter(
            models.UserBlock.blocker_id == current_user.id,
            models.UserBlock.blocked_id == target.id,
        )
        .first()
    )
    if block is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User is not blocked",
        )
    database.delete(block)
    database.commit()
    return {"detail": "User unblocked"}


@app.get("/api/users/{user_id}", response_model=UserProfileResponse)
def get_user_profile(
    user_id: int,
    database: Session = Depends(get_db),
) -> UserProfileResponse:
    user = database.get(models.User, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    post_count = (
        database.query(func.count(models.Post.id))
        .filter(
            models.Post.author_id == user_id,
            models.Post.is_deleted.is_(False),
        )
        .scalar()
    )
    comment_count = (
        database.query(func.count(models.Comment.id))
        .filter(
            models.Comment.author_id == user_id,
            models.Comment.is_deleted.is_(False),
        )
        .scalar()
    )
    karma = (
        database.query(func.coalesce(func.sum(models.Vote.value), 0))
        .join(models.Post, models.Vote.post_id == models.Post.id)
        .filter(
            models.Post.author_id == user_id,
            models.Post.is_deleted.is_(False),
        )
        .scalar()
    )
    follower_count = (
        database.query(func.count(models.UserFollow.id))
        .filter(models.UserFollow.following_id == user_id)
        .scalar()
    )
    following_count = (
        database.query(func.count(models.UserFollow.id))
        .filter(models.UserFollow.follower_id == user_id)
        .scalar()
    )
    return UserProfileResponse(
        id=user.id,
        username=user.username,
        created_at=user.created_at,
        post_count=post_count or 0,
        comment_count=comment_count or 0,
        karma=karma or 0,
        follower_count=follower_count or 0,
        following_count=following_count or 0,
    )


@app.get("/api/users/{user_id}/posts", response_model=list[UserPostItem])
def list_user_posts(
    user_id: int,
    database: Session = Depends(get_db),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> list[UserPostItem]:
    user = database.get(models.User, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    vote_counts = (
        database.query(
            models.Vote.post_id,
            func.sum(case((models.Vote.value == 1, 1), else_=0)).label("upvotes"),
            func.sum(case((models.Vote.value == -1, 1), else_=0)).label("downvotes"),
        )
        .group_by(models.Vote.post_id)
        .subquery()
    )
    comment_counts = (
        database.query(
            models.Comment.post_id,
            func.count(models.Comment.id).label("comment_count"),
        )
        .filter(models.Comment.is_deleted.is_(False))
        .group_by(models.Comment.post_id)
        .subquery()
    )
    rows = (
        database.query(
            models.Post,
            models.Community.name,
            func.coalesce(vote_counts.c.upvotes, 0).label("upvotes"),
            func.coalesce(vote_counts.c.downvotes, 0).label("downvotes"),
            func.coalesce(comment_counts.c.comment_count, 0).label("comment_count"),
        )
        .join(models.Community, models.Post.community_id == models.Community.id)
        .outerjoin(vote_counts, models.Post.id == vote_counts.c.post_id)
        .outerjoin(comment_counts, models.Post.id == comment_counts.c.post_id)
        .filter(
            models.Post.author_id == user_id,
            models.Post.is_deleted.is_(False),
        )
        .order_by(models.Post.created_at.desc(), models.Post.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return [
        UserPostItem(
            id=post.id,
            title=post.title,
            content=post.content,
            community_name=community_name,
            created_at=post.created_at,
            upvotes=upvotes,
            downvotes=downvotes,
            score=upvotes - downvotes,
            comment_count=comment_count,
        )
        for post, community_name, upvotes, downvotes, comment_count in rows
    ]


@app.post(
    "/api/communities",
    response_model=CommunityResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_community(
    community: CommunityCreate,
    database: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> models.Community:
    new_community = models.Community(
        name=community.name,
        description=community.description,
        creator_id=current_user.id,
    )
    database.add(new_community)
    try:
        database.commit()
    except IntegrityError as error:
        database.rollback()
        if "communities.name" in str(error.orig):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Community name already exists",
            )
        raise
    database.refresh(new_community)
    return get_community_response(database, new_community)


def get_community_response(
    database: Session,
    community: models.Community,
) -> CommunityResponse:
    member_count = (
        database.query(func.count(models.CommunityMember.id))
        .filter(models.CommunityMember.community_id == community.id)
        .scalar()
    )
    return CommunityResponse(
        id=community.id,
        name=community.name,
        description=community.description,
        creator_id=community.creator_id,
        created_at=community.created_at,
        member_count=member_count or 0,
    )


@app.get("/api/communities", response_model=list[CommunityResponse])
def list_communities(database: Session = Depends(get_db)) -> list[CommunityResponse]:
    communities = database.query(models.Community).order_by(models.Community.id).all()
    return [get_community_response(database, community) for community in communities]


@app.post(
    "/api/communities/{community_id}/join",
    response_model=CommunityMemberResponse,
    status_code=status.HTTP_201_CREATED,
)
def join_community(
    community_id: int,
    database: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> CommunityMemberResponse:
    community = database.get(models.Community, community_id)
    if community is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Community not found",
        )
    existing_member = (
        database.query(models.CommunityMember)
        .filter(
            models.CommunityMember.user_id == current_user.id,
            models.CommunityMember.community_id == community_id,
        )
        .first()
    )
    if existing_member is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="User is already a community member",
        )

    member = models.CommunityMember(
        user_id=current_user.id,
        community_id=community.id,
    )
    database.add(member)
    database.commit()
    database.refresh(member)
    return CommunityMemberResponse(
        id=current_user.id,
        username=current_user.username,
        joined_at=member.joined_at,
    )


@app.post("/api/communities/{community_id}/leave", status_code=status.HTTP_200_OK)
def leave_community(
    community_id: int,
    database: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> dict[str, str]:
    community = database.get(models.Community, community_id)
    if community is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Community not found",
        )
    member = (
        database.query(models.CommunityMember)
        .filter(
            models.CommunityMember.user_id == current_user.id,
            models.CommunityMember.community_id == community_id,
        )
        .first()
    )
    if member is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User is not a community member",
        )
    database.delete(member)
    database.commit()
    return {"detail": "Left community"}


@app.get(
    "/api/communities/{community_id}/members",
    response_model=list[CommunityMemberResponse],
)
def list_community_members(
    community_id: int,
    database: Session = Depends(get_db),
) -> list[CommunityMemberResponse]:
    community = database.get(models.Community, community_id)
    if community is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Community not found",
        )
    rows = (
        database.query(
            models.User.id,
            models.User.username,
            models.CommunityMember.joined_at,
        )
        .join(models.User, models.CommunityMember.user_id == models.User.id)
        .filter(models.CommunityMember.community_id == community_id)
        .order_by(models.CommunityMember.id)
        .all()
    )
    return [
        CommunityMemberResponse(
            id=user_id,
            username=username,
            joined_at=joined_at,
        )
        for user_id, username, joined_at in rows
    ]


def get_post_response(database: Session, post_id: int) -> PostResponse:
    result = (
        database.query(models.Post, models.User.username, models.Community.name)
        .join(models.User, models.Post.author_id == models.User.id)
        .join(models.Community, models.Post.community_id == models.Community.id)
        .filter(models.Post.id == post_id)
        .one()
    )
    post, author_username, community_name = result
    return PostResponse(
        id=post.id,
        title=post.title,
        content=post.content,
        author_id=post.author_id,
        community_id=post.community_id,
        created_at=post.created_at,
        author_username=author_username,
        community_name=community_name,
    )


@app.post(
    "/api/posts",
    response_model=PostResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_post(
    post: PostCreate,
    database: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> PostResponse:
    community = database.get(models.Community, post.community_id)
    if community is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Community not found",
        )

    new_post = models.Post(
        title=post.title,
        content=post.content,
        author_id=current_user.id,
        community_id=community.id,
    )
    database.add(new_post)
    database.commit()
    database.refresh(new_post)
    return get_post_response(database, new_post.id)


@app.delete("/api/posts/{post_id}")
def delete_post(
    post_id: int,
    database: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> dict[str, str]:
    post = database.get(models.Post, post_id)
    if post is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Post not found",
        )
    if post.author_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the post author can delete this post",
        )
    post.is_deleted = True
    post.deleted_at = datetime.now(timezone.utc)
    database.commit()
    return {"detail": "Post deleted"}


@app.get(
    "/api/communities/{community_id}/posts",
    response_model=list[PostResponse],
)
def list_community_posts(
    community_id: int,
    database: Session = Depends(get_db),
) -> list[PostResponse]:
    community = database.get(models.Community, community_id)
    if community is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Community not found",
        )

    rows = (
        database.query(models.Post, models.User.username, models.Community.name)
        .join(models.User, models.Post.author_id == models.User.id)
        .join(models.Community, models.Post.community_id == models.Community.id)
        .filter(
            models.Post.community_id == community_id,
            models.Post.is_deleted.is_(False),
        )
        .order_by(models.Post.id)
        .all()
    )
    return [
        PostResponse(
            id=post.id,
            title=post.title,
            content=post.content,
            author_id=post.author_id,
            community_id=post.community_id,
            created_at=post.created_at,
            author_username=author_username,
            community_name=community_name,
        )
        for post, author_username, community_name in rows
    ]


def get_comment_response(database: Session, comment_id: int) -> CommentResponse:
    comment, author_username = (
        database.query(models.Comment, models.User.username)
        .join(models.User, models.Comment.author_id == models.User.id)
        .filter(models.Comment.id == comment_id)
        .one()
    )
    return CommentResponse(
        id=comment.id,
        content=comment.content,
        author_id=comment.author_id,
        post_id=comment.post_id,
        created_at=comment.created_at,
        author_username=author_username,
    )


@app.post(
    "/api/posts/{post_id}/comments",
    response_model=CommentResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_comment(
    post_id: int,
    comment: CommentCreate,
    database: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> CommentResponse:
    post = database.get(models.Post, post_id)
    if post is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Post not found",
        )
    if is_user_blocked(database, post.author_id, current_user.id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are blocked by the post author",
        )

    new_comment = models.Comment(
        content=comment.content,
        author_id=current_user.id,
        post_id=post.id,
    )
    database.add(new_comment)
    create_notification(
        database=database,
        recipient_id=post.author_id,
        actor=current_user,
        post_id=post.id,
        notification_type="comment",
        message=f"{current_user.username} commented on your post",
    )
    database.commit()
    database.refresh(new_comment)
    return get_comment_response(database, new_comment.id)


@app.get(
    "/api/posts/{post_id}/comments",
    response_model=list[CommentResponse],
)
def list_post_comments(
    post_id: int,
    database: Session = Depends(get_db),
) -> list[CommentResponse]:
    post = database.get(models.Post, post_id)
    if post is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Post not found",
        )

    rows = (
        database.query(models.Comment, models.User.username)
        .join(models.User, models.Comment.author_id == models.User.id)
        .filter(
            models.Comment.post_id == post_id,
            models.Comment.is_deleted.is_(False),
        )
        .order_by(models.Comment.id)
        .all()
    )
    return [
        CommentResponse(
            id=comment.id,
            content=comment.content,
            author_id=comment.author_id,
            post_id=comment.post_id,
            created_at=comment.created_at,
            author_username=author_username,
        )
        for comment, author_username in rows
    ]


@app.delete("/api/comments/{comment_id}")
def delete_comment(
    comment_id: int,
    database: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> dict[str, str]:
    comment = database.get(models.Comment, comment_id)
    if comment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Comment not found",
        )
    if comment.author_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the comment author can delete this comment",
        )
    comment.is_deleted = True
    comment.deleted_at = datetime.now(timezone.utc)
    database.commit()
    return {"detail": "Comment deleted"}


def get_vote_summary(database: Session, post_id: int) -> VoteSummary:
    votes = database.query(models.Vote).filter(models.Vote.post_id == post_id).all()
    upvotes = sum(vote.value == 1 for vote in votes)
    downvotes = sum(vote.value == -1 for vote in votes)
    return VoteSummary(
        upvotes=upvotes,
        downvotes=downvotes,
        score=upvotes - downvotes,
    )


@app.post("/api/posts/{post_id}/vote", response_model=VoteSummary)
def vote_on_post(
    post_id: int,
    vote: VoteCreate,
    database: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> VoteSummary:
    post = database.get(models.Post, post_id)
    if post is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Post not found",
        )
    if is_user_blocked(database, post.author_id, current_user.id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are blocked by the post author",
        )

    existing_vote = (
        database.query(models.Vote)
        .filter(
            models.Vote.user_id == current_user.id,
            models.Vote.post_id == post_id,
        )
        .first()
    )
    should_notify = existing_vote is None or (
        existing_vote is not None and existing_vote.value != vote.value
    )
    if existing_vote is None:
        database.add(
            models.Vote(
                user_id=current_user.id,
                post_id=post_id,
                value=vote.value,
            )
        )
    elif existing_vote.value == vote.value:
        database.delete(existing_vote)
    else:
        existing_vote.value = vote.value

    if should_notify:
        create_notification(
            database=database,
            recipient_id=post.author_id,
            actor=current_user,
            post_id=post.id,
            notification_type="vote",
            message=f"{current_user.username} voted on your post",
        )
    database.commit()
    return get_vote_summary(database, post_id)


@app.get("/api/posts/{post_id}/votes", response_model=VoteSummary)
def get_post_votes(
    post_id: int,
    database: Session = Depends(get_db),
) -> VoteSummary:
    post = database.get(models.Post, post_id)
    if post is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Post not found",
        )
    return get_vote_summary(database, post_id)


@app.get("/api/notifications", response_model=list[NotificationResponse])
def list_notifications(
    database: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> list[NotificationResponse]:
    rows = (
        database.query(models.Notification, models.User.username)
        .join(models.User, models.Notification.actor_id == models.User.id)
        .filter(models.Notification.recipient_id == current_user.id)
        .order_by(models.Notification.created_at.desc(), models.Notification.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return [
        NotificationResponse(
            id=notification.id,
            actor_username=actor_username,
            post_id=notification.post_id,
            type=notification.type,
            message=notification.message,
            is_read=notification.is_read,
            created_at=notification.created_at,
        )
        for notification, actor_username in rows
    ]


@app.post("/api/notifications/{notification_id}/read")
def mark_notification_read(
    notification_id: int,
    database: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> dict[str, str]:
    notification = (
        database.query(models.Notification)
        .filter(
            models.Notification.id == notification_id,
            models.Notification.recipient_id == current_user.id,
        )
        .first()
    )
    if notification is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Notification not found",
        )
    notification.is_read = True
    database.commit()
    return {"detail": "Notification marked as read"}


def build_report_response(report: models.Report) -> ReportResponse:
    target_type = "post" if report.post_id is not None else "comment"
    return ReportResponse(
        id=report.id,
        target_type=target_type,
        post_id=report.post_id,
        comment_id=report.comment_id,
        reason=report.reason,
        status=report.status,
        created_at=report.created_at,
    )


@app.post("/api/reports", response_model=ReportResponse, status_code=status.HTTP_201_CREATED)
def create_report(
    report: ReportCreate,
    database: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> ReportResponse:
    if (report.post_id is None) == (report.comment_id is None):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Report must target exactly one post or comment",
        )

    target_post = None
    target_comment = None
    if report.post_id is not None:
        target_post = database.get(models.Post, report.post_id)
        if target_post is None or target_post.is_deleted:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Post not found",
            )
        if target_post.author_id == current_user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Users cannot report their own post",
            )
        duplicate_query = database.query(models.Report).filter(
            models.Report.reporter_id == current_user.id,
            models.Report.post_id == report.post_id,
            models.Report.status == "pending",
        )
    else:
        target_comment = database.get(models.Comment, report.comment_id)
        if target_comment is None or target_comment.is_deleted:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Comment not found",
            )
        if target_comment.author_id == current_user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Users cannot report their own comment",
            )
        duplicate_query = database.query(models.Report).filter(
            models.Report.reporter_id == current_user.id,
            models.Report.comment_id == report.comment_id,
            models.Report.status == "pending",
        )

    if duplicate_query.first() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A pending report already exists for this target",
        )

    new_report = models.Report(
        reporter_id=current_user.id,
        post_id=report.post_id,
        comment_id=report.comment_id,
        reason=report.reason,
    )
    database.add(new_report)
    database.commit()
    database.refresh(new_report)
    return build_report_response(new_report)


@app.get("/api/reports/mine", response_model=list[ReportResponse])
def list_my_reports(
    database: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> list[ReportResponse]:
    reports = (
        database.query(models.Report)
        .filter(models.Report.reporter_id == current_user.id)
        .order_by(models.Report.created_at.desc(), models.Report.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return [build_report_response(report) for report in reports]


def build_admin_report_response(
    report: models.Report,
    reporter_username: str,
) -> AdminReportResponse:
    target_type = "post" if report.post_id is not None else "comment"
    return AdminReportResponse(
        id=report.id,
        reporter_username=reporter_username,
        target_type=target_type,
        post_id=report.post_id,
        comment_id=report.comment_id,
        reason=report.reason,
        status=report.status,
        created_at=report.created_at,
    )


@app.get("/api/admin/reports", response_model=list[AdminReportResponse])
def list_admin_reports(
    database: Session = Depends(get_db),
    current_admin: models.User = Depends(get_current_admin),
    status_filter: Literal["pending", "reviewed", "dismissed", "actioned"] | None = Query(
        default=None,
        alias="status",
    ),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> list[AdminReportResponse]:
    query = (
        database.query(models.Report, models.User.username)
        .join(models.User, models.Report.reporter_id == models.User.id)
        .order_by(models.Report.created_at.desc(), models.Report.id.desc())
    )
    if status_filter is not None:
        query = query.filter(models.Report.status == status_filter)
    rows = query.offset(offset).limit(limit).all()
    return [
        build_admin_report_response(report, reporter_username)
        for report, reporter_username in rows
    ]


@app.post(
    "/api/admin/reports/{report_id}/review",
    response_model=AdminReportResponse,
)
def review_report(
    report_id: int,
    review: ReviewReportRequest,
    database: Session = Depends(get_db),
    current_admin: models.User = Depends(get_current_admin),
) -> AdminReportResponse:
    report = database.get(models.Report, report_id)
    if report is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Report not found",
        )
    report.status = review.status
    database.commit()
    database.refresh(report)
    reporter_username = database.get(models.User, report.reporter_id).username
    return build_admin_report_response(report, reporter_username)


@app.delete("/api/admin/posts/{post_id}")
def admin_delete_post(
    post_id: int,
    database: Session = Depends(get_db),
    current_admin: models.User = Depends(get_current_admin),
) -> dict[str, str]:
    post = database.get(models.Post, post_id)
    if post is None or post.is_deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Post not found",
        )
    post.is_deleted = True
    post.deleted_at = datetime.now(timezone.utc)
    database.commit()
    return {"detail": "Post deleted"}


@app.delete("/api/admin/comments/{comment_id}")
def admin_delete_comment(
    comment_id: int,
    database: Session = Depends(get_db),
    current_admin: models.User = Depends(get_current_admin),
) -> dict[str, str]:
    comment = database.get(models.Comment, comment_id)
    if comment is None or comment.is_deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Comment not found",
        )
    comment.is_deleted = True
    comment.deleted_at = datetime.now(timezone.utc)
    database.commit()
    return {"detail": "Comment deleted"}


@app.get("/api/feed", response_model=list[FeedItem])
def get_feed(
    database: Session = Depends(get_db),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> list[FeedItem]:
    vote_counts = (
        database.query(
            models.Vote.post_id,
            func.sum(case((models.Vote.value == 1, 1), else_=0)).label("upvotes"),
            func.sum(case((models.Vote.value == -1, 1), else_=0)).label("downvotes"),
        )
        .group_by(models.Vote.post_id)
        .subquery()
    )
    comment_counts = (
        database.query(
            models.Comment.post_id,
            func.count(models.Comment.id).label("comment_count"),
        )
        .filter(models.Comment.is_deleted.is_(False))
        .group_by(models.Comment.post_id)
        .subquery()
    )
    rows = (
        database.query(
            models.Post,
            models.User.username,
            models.Community.name,
            func.coalesce(vote_counts.c.upvotes, 0).label("upvotes"),
            func.coalesce(vote_counts.c.downvotes, 0).label("downvotes"),
            func.coalesce(comment_counts.c.comment_count, 0).label("comment_count"),
        )
        .join(models.User, models.Post.author_id == models.User.id)
        .join(models.Community, models.Post.community_id == models.Community.id)
        .outerjoin(vote_counts, models.Post.id == vote_counts.c.post_id)
        .outerjoin(comment_counts, models.Post.id == comment_counts.c.post_id)
        .filter(models.Post.is_deleted.is_(False))
        .order_by(models.Post.created_at.desc(), models.Post.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return [
        FeedItem(
            id=post.id,
            title=post.title,
            content=post.content,
            author_username=author_username,
            community_name=community_name,
            created_at=post.created_at,
            upvotes=upvotes,
            downvotes=downvotes,
            score=upvotes - downvotes,
            comment_count=comment_count,
        )
        for (
            post,
            author_username,
            community_name,
            upvotes,
            downvotes,
            comment_count,
        ) in rows
    ]


def search_posts(
    database: Session,
    query: str,
    limit: int,
    offset: int,
) -> list[SearchPostResult]:
    vote_counts = (
        database.query(
            models.Vote.post_id,
            func.sum(case((models.Vote.value == 1, 1), else_=0)).label("upvotes"),
            func.sum(case((models.Vote.value == -1, 1), else_=0)).label("downvotes"),
        )
        .group_by(models.Vote.post_id)
        .subquery()
    )
    comment_counts = (
        database.query(
            models.Comment.post_id,
            func.count(models.Comment.id).label("comment_count"),
        )
        .filter(models.Comment.is_deleted.is_(False))
        .group_by(models.Comment.post_id)
        .subquery()
    )
    rows = (
        database.query(
            models.Post,
            models.User.username,
            models.Community.name,
            func.coalesce(vote_counts.c.upvotes, 0).label("upvotes"),
            func.coalesce(vote_counts.c.downvotes, 0).label("downvotes"),
            func.coalesce(comment_counts.c.comment_count, 0).label("comment_count"),
        )
        .join(models.User, models.Post.author_id == models.User.id)
        .join(models.Community, models.Post.community_id == models.Community.id)
        .outerjoin(vote_counts, models.Post.id == vote_counts.c.post_id)
        .outerjoin(comment_counts, models.Post.id == comment_counts.c.post_id)
        .filter(
            models.Post.is_deleted.is_(False),
            models.Post.title.ilike(f"%{query}%")
            | models.Post.content.ilike(f"%{query}%")
        )
        .order_by(models.Post.created_at.desc(), models.Post.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return [
        SearchPostResult(
            id=post.id,
            title=post.title,
            content=post.content,
            author_username=author_username,
            community_name=community_name,
            created_at=post.created_at,
            score=upvotes - downvotes,
            comment_count=comment_count,
        )
        for post, author_username, community_name, upvotes, downvotes, comment_count in rows
    ]


def search_communities(
    database: Session,
    query: str,
    limit: int,
    offset: int,
) -> list[SearchCommunityResult]:
    rows = (
        database.query(models.Community, models.User.username)
        .join(models.User, models.Community.creator_id == models.User.id)
        .filter(
            models.Community.name.ilike(f"%{query}%")
            | models.Community.description.ilike(f"%{query}%")
        )
        .order_by(models.Community.created_at.desc(), models.Community.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return [
        SearchCommunityResult(
            id=community.id,
            name=community.name,
            description=community.description,
            creator_username=creator_username,
            created_at=community.created_at,
        )
        for community, creator_username in rows
    ]


def search_users(
    database: Session,
    query: str,
    limit: int,
    offset: int,
) -> list[SearchUserResult]:
    users = (
        database.query(models.User)
        .filter(models.User.username.ilike(f"%{query}%"))
        .order_by(models.User.created_at.desc(), models.User.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return [
        SearchUserResult(
            id=user.id,
            username=user.username,
            created_at=user.created_at,
        )
        for user in users
    ]


@app.get("/api/search")
def search(
    q: str = Query(..., min_length=1),
    search_type: Literal["posts", "communities", "users", "all"] = Query(
        default="all", alias="type"
    ),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    database: Session = Depends(get_db),
) -> list[SearchPostResult] | list[SearchCommunityResult] | list[SearchUserResult] | dict[str, list]:
    query = q.strip()
    if not query:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Query must not be empty",
        )
    if search_type == "posts":
        return search_posts(database, query, limit, offset)
    if search_type == "communities":
        return search_communities(database, query, limit, offset)
    if search_type == "users":
        return search_users(database, query, limit, offset)
    return {
        "posts": search_posts(database, query, limit, offset),
        "communities": search_communities(database, query, limit, offset),
        "users": search_users(database, query, limit, offset),
    }


@app.post(
    "/api/auth/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
)
def register_user(
    user: RegistrationRequest,
    database: Session = Depends(get_db),
) -> models.User:
    return save_user(user, database, password_hash.hash(user.password))


@app.post("/api/auth/login", response_model=TokenResponse)
def login_user(
    credentials: LoginRequest,
    database: Session = Depends(get_db),
) -> TokenResponse:
    user = database.query(models.User).filter(models.User.email == str(credentials.email)).first()
    if user is None or user.password_hash is None or not password_hash.verify(
        credentials.password, user.password_hash
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )
    return TokenResponse(
        access_token=create_access_token(user),
        token_type="bearer",
    )
