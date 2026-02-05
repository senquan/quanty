from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import joinedload
from app.core.database import get_db
from app.models.user import User
from app.models.role import Role
from app.schemas.user import UserCreate, UserLogin, Token
from app.schemas.response import Response
from app.core.security import create_access_token, verify_password, get_password_hash, verify_token, create_refresh_token

router = APIRouter()

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")

async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db)
) -> User:
    """获取当前认证用户"""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="无效的认证凭据",
        headers={"WWW-Authenticate": "Bearer"},
    )
    username = verify_token(token)
    if username is None:
        raise credentials_exception
    
    # Async: 使用 select + joinedload + await db.execute
    result = await db.execute(
        select(User)
        # .options(joinedload(User.role).joinedload(Role.permissions))
        .where(User.username == username)
    )
    user = result.scalars().first()
    
    if user is None:
        raise credentials_exception
    
    return user

@router.post("/register", response_model=Response[dict])
async def register(user_data: UserCreate, db: AsyncSession = Depends(get_db)):
    # 检查用户是否已存在
    result = await db.execute(select(User).where(User.email == user_data.email))
    existing_user = result.scalars().first()
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="邮箱已被注册"
        )
    
    # 创建新用户
    hashed_password = get_password_hash(user_data.password)
    user = User(
        email=user_data.email,
        username=user_data.username,
        hashed_password=hashed_password
    )
    
    db.add(user)
    await db.commit()
    await db.refresh(user)
    
    return Response.success(data={"message": "用户注册成功", "user_id": user.id})

@router.post("/login", response_model=Response[Token])
async def login(form_data: OAuth2PasswordRequestForm = Depends(), db: AsyncSession = Depends(get_db)):
    # 验证用户
    result = await db.execute(select(User).where(User.username == form_data.username))
    user = result.scalars().first()
    
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误"
        )
    
    # 创建访问令牌
    access_token = create_access_token(data={"sub": user.username})
    # 创建刷新令牌
    refresh_token = create_refresh_token(data={"sub": user.username})
    
    return Response.success(data={
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer"
    })

@router.get("/me", response_model=Response[dict])
async def get_current_user_info(current_user: User = Depends(get_current_user)):
    """获取当前用户信息"""
    user_data = {
        "id": current_user.id,
        "username": current_user.username,
        "email": current_user.email,
        "realname": current_user.realname, # User model defined realname
        "is_active": current_user.status == 1, # User model uses status=1 for active
        "created_at": current_user.created_at.isoformat() if current_user.created_at else None
    }
    
    # 如果有角色信息，也一并返回
    # if current_user.role:
    #     user_data["role"] = {
    #         "id": current_user.role.id,
    #         "name": current_user.role.name,
    #         "permissions": [perm.permission for perm in current_user.role.permissions] if current_user.role.permissions else []
    #     }
    
    return Response.success(data=user_data)
